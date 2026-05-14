import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

import aiosqlite
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, validator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration (task 1.2) ---
DATABASE_PATH = os.getenv("DATABASE_PATH", "/data/teams.db")
GRAFANA_BASE_URL = os.getenv("GRAFANA_BASE_URL", "")
GRAFANA_DASHBOARD_PATH = os.getenv("GRAFANA_DASHBOARD_PATH", "")
ARGO_ROLLOUTS_BASE_URL = os.getenv("ARGO_ROLLOUTS_BASE_URL", "")

_stop_event = threading.Event()
_watcher_threads: list = []


# --- Database helpers ---

def init_db():
    """Create schema and enable WAL mode (task 2.1, 5.1, 5.2)."""
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS teams (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_name_lower
            ON teams (LOWER(name));

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            team_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            resource TEXT NOT NULL,
            namespace TEXT NOT NULL,
            message TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            links TEXT NOT NULL DEFAULT '[]'
        );

        CREATE INDEX IF NOT EXISTS idx_events_team_timestamp
            ON events (team_id, timestamp DESC);
    """)
    conn.commit()
    conn.close()


def _sync_write_event(team_id: str, event_type: str, severity: str, resource: str,
                      namespace: str, message: str, links: list):
    """Insert an event row using a sync sqlite3 connection (task 7.6).
    Called from watcher threads — uses sqlite3 directly so WAL handles concurrency."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute(
        """INSERT INTO events (id, team_id, event_type, severity, resource, namespace, message, timestamp, links)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), team_id, event_type, severity, resource, namespace,
         message, datetime.now(timezone.utc).isoformat(), json.dumps(links))
    )
    conn.commit()
    conn.close()


# --- Kubernetes helpers ---

def _load_k8s_config():
    from kubernetes import config
    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()


def resolve_team_id(namespace_name: str, core_v1) -> Optional[str]:
    """Read teams.example.com/team-id label from the namespace (task 7.1)."""
    try:
        ns = core_v1.read_namespace(namespace_name)
        labels = ns.metadata.labels or {}
        return labels.get("teams.example.com/team-id")
    except Exception as e:
        logger.debug(f"Could not resolve team ID for namespace {namespace_name}: {e}")
        return None


def build_links(event_type: str, namespace: str, resource: str, core_v1) -> list:
    """Construct contextual links from env vars at event-capture time (tasks 6.1–6.6)."""
    links = []

    grafana_url = None
    if GRAFANA_BASE_URL and GRAFANA_DASHBOARD_PATH:
        grafana_url = f"{GRAFANA_BASE_URL}{GRAFANA_DASHBOARD_PATH}?var-namespace={namespace}"

    if event_type == "deployment.succeeded":
        # task 6.2: Grafana link + Ingress host
        if grafana_url:
            links.append({"label": "Grafana Dashboard", "url": grafana_url})
        try:
            ingresses = core_v1.list_namespaced_ingress(namespace)
            for ingress in ingresses.items:
                for rule in (ingress.spec.rules or []):
                    if rule.host:
                        links.append({"label": "App URL", "url": f"http://{rule.host}"})
                        break
                if any(l["label"] == "App URL" for l in links):
                    break
        except Exception as e:
            logger.debug(f"No ingress in namespace {namespace}: {e}")

    elif event_type == "deployment.failed":
        # task 6.3: Grafana link only
        if grafana_url:
            links.append({"label": "Grafana Dashboard", "url": grafana_url})

    elif event_type.startswith("rollout."):
        # task 6.4: Argo Rollouts UI link + Grafana
        if ARGO_ROLLOUTS_BASE_URL:
            links.append({
                "label": "Argo Rollouts UI",
                "url": f"{ARGO_ROLLOUTS_BASE_URL}/rollouts/rollout/{namespace}/{resource}"
            })
        if grafana_url:
            links.append({"label": "Grafana Dashboard", "url": grafana_url})

    elif event_type == "gatekeeper.violation":
        # task 6.5: Grafana link only
        if grafana_url:
            links.append({"label": "Grafana Dashboard", "url": grafana_url})

    # task 6.6: links with missing base URL are already excluded above
    return links


# --- Kubernetes watcher threads ---

def watch_deployments():
    """Stream Deployment watch events and emit deployment.succeeded/failed (task 7.2)."""
    from kubernetes import client, watch
    _load_k8s_config()
    apps_v1 = client.AppsV1Api()
    core_v1 = client.CoreV1Api()

    logger.info("Starting deployment watcher")
    while not _stop_event.is_set():
        try:
            w = watch.Watch()
            for event in w.stream(apps_v1.list_deployment_for_all_namespaces, timeout_seconds=60):
                if _stop_event.is_set():
                    w.stop()
                    break

                if event["type"] not in ("MODIFIED",):
                    continue

                deployment = event["object"]
                namespace = deployment.metadata.namespace
                name = deployment.metadata.name

                team_id = resolve_team_id(namespace, core_v1)
                if not team_id:
                    continue

                conditions = (deployment.status.conditions or []) if deployment.status else []
                for condition in conditions:
                    if condition.type == "Available" and condition.status == "True":
                        links = build_links("deployment.succeeded", namespace, name, core_v1)
                        _sync_write_event(
                            team_id=team_id,
                            event_type="deployment.succeeded",
                            severity="info",
                            resource=name,
                            namespace=namespace,
                            message=f"Deployment '{name}' is available",
                            links=links,
                        )
                        break
                    elif (condition.type == "Progressing"
                          and condition.status == "False"
                          and condition.reason == "ProgressDeadlineExceeded"):
                        links = build_links("deployment.failed", namespace, name, core_v1)
                        _sync_write_event(
                            team_id=team_id,
                            event_type="deployment.failed",
                            severity="error",
                            resource=name,
                            namespace=namespace,
                            message=f"Deployment '{name}' failed: {condition.message}",
                            links=links,
                        )
                        break
        except Exception as e:
            if not _stop_event.is_set():
                logger.error(f"Deployment watcher error: {e}")
                _stop_event.wait(timeout=5)


def watch_rollouts():
    """Stream Argo Rollout CRD events (task 7.3). Logs warning if CRD absent."""
    from kubernetes import client, watch
    _load_k8s_config()
    custom_api = client.CustomObjectsApi()
    core_v1 = client.CoreV1Api()

    logger.info("Starting rollout watcher")
    while not _stop_event.is_set():
        try:
            w = watch.Watch()
            for event in w.stream(
                custom_api.list_cluster_custom_object,
                group="argoproj.io",
                version="v1alpha1",
                plural="rollouts",
                timeout_seconds=60,
            ):
                if _stop_event.is_set():
                    w.stop()
                    break

                if event["type"] not in ("MODIFIED",):
                    continue

                rollout = event["object"]
                namespace = rollout["metadata"]["namespace"]
                name = rollout["metadata"]["name"]
                phase = rollout.get("status", {}).get("phase", "")

                team_id = resolve_team_id(namespace, core_v1)
                if not team_id:
                    continue

                if phase == "Progressing":
                    event_type, severity = "rollout.in_progress", "info"
                    message = f"Rollout '{name}' is in progress"
                elif phase == "Degraded":
                    event_type, severity = "rollout.degraded", "error"
                    message = f"Rollout '{name}' is degraded"
                elif phase == "Healthy":
                    event_type, severity = "rollout.healthy", "info"
                    message = f"Rollout '{name}' is healthy"
                else:
                    continue

                links = build_links(event_type, namespace, name, core_v1)
                _sync_write_event(
                    team_id=team_id,
                    event_type=event_type,
                    severity=severity,
                    resource=name,
                    namespace=namespace,
                    message=message,
                    links=links,
                )

        except Exception as e:
            err_str = str(e).lower()
            if "404" in err_str or "not found" in err_str:
                logger.warning("Argo Rollouts CRD not found — rollout watcher disabled")
                return
            if not _stop_event.is_set():
                logger.error(f"Rollout watcher error: {e}")
                _stop_event.wait(timeout=5)


def watch_gatekeeper_events():
    """Stream k8s Events filtered for Gatekeeper violations (task 7.4)."""
    from kubernetes import client, watch
    _load_k8s_config()
    core_v1 = client.CoreV1Api()

    logger.info("Starting gatekeeper events watcher")
    while not _stop_event.is_set():
        try:
            w = watch.Watch()
            for event in w.stream(core_v1.list_event_for_all_namespaces, timeout_seconds=60):
                if _stop_event.is_set():
                    w.stop()
                    break

                k8s_event = event["object"]
                source_component = ""
                if k8s_event.source and k8s_event.source.component:
                    source_component = k8s_event.source.component
                reason = k8s_event.reason or ""

                if source_component != "gatekeeper" and reason != "FailedAdmission":
                    continue

                involved = k8s_event.involved_object
                resource_name = involved.name if involved else "unknown"
                message = k8s_event.message or "Gatekeeper policy violation"

                # Gatekeeper emits events in gatekeeper-system; the affected team
                # namespace is embedded in the message as "Resource Namespace: <ns>"
                ns_match = re.search(r"Resource Namespace:\s*([^\s,]+)", message)
                namespace = (ns_match.group(1) if ns_match
                             else (involved.namespace if involved and involved.namespace
                                   else k8s_event.metadata.namespace or ""))

                team_id = resolve_team_id(namespace, core_v1)
                if not team_id:
                    continue
                message = k8s_event.message or "Gatekeeper policy violation"

                links = build_links("gatekeeper.violation", namespace, resource_name, core_v1)
                _sync_write_event(
                    team_id=team_id,
                    event_type="gatekeeper.violation",
                    severity="warning",
                    resource=resource_name,
                    namespace=namespace,
                    message=message,
                    links=links,
                )
        except Exception as e:
            if not _stop_event.is_set():
                logger.error(f"Gatekeeper events watcher error: {e}")
                _stop_event.wait(timeout=5)


def poll_gatekeeper_constraints():
    """Poll Gatekeeper constraint .status.violations every 15s (task 7.5)."""
    from kubernetes import client
    _load_k8s_config()
    custom_api = client.CustomObjectsApi()
    ext_api = client.ApiextensionsV1Api()
    core_v1 = client.CoreV1Api()

    logger.info("Starting gatekeeper constraint poller")
    while not _stop_event.is_set():
        try:
            crds = ext_api.list_custom_resource_definition()
            constraint_crds = [
                crd for crd in crds.items
                if "constraints.gatekeeper.sh" in (crd.spec.group or "")
            ]

            seen_violations: set = set()

            for crd in constraint_crds:
                group = crd.spec.group
                version = crd.spec.versions[0].name if crd.spec.versions else "v1beta1"
                plural = crd.spec.names.plural
                constraint_name = crd.metadata.name

                try:
                    constraints = custom_api.list_cluster_custom_object(
                        group=group, version=version, plural=plural
                    )
                    for constraint in constraints.get("items", []):
                        violations = constraint.get("status", {}).get("violations") or []
                        for violation in violations:
                            namespace = violation.get("namespace", "")
                            if not namespace:
                                continue

                            team_id = resolve_team_id(namespace, core_v1)
                            if not team_id:
                                continue

                            resource_name = violation.get("name", "unknown")
                            message = violation.get("message", "Gatekeeper policy violation")

                            key = f"{constraint_name}/{namespace}/{resource_name}"
                            if key in seen_violations:
                                continue
                            seen_violations.add(key)

                            links = build_links("gatekeeper.violation", namespace, resource_name, core_v1)
                            _sync_write_event(
                                team_id=team_id,
                                event_type="gatekeeper.violation",
                                severity="warning",
                                resource=resource_name,
                                namespace=namespace,
                                message=message,
                                links=links,
                            )
                except Exception as e:
                    logger.warning(f"Error polling constraint {plural}: {e}")

        except Exception as e:
            logger.error(f"Gatekeeper constraint poll error: {e}")

        _stop_event.wait(timeout=15)


# --- FastAPI lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    # task 2.3, 2.5: initialise DB and start watchers
    init_db()

    watcher_funcs = [
        watch_deployments,
        watch_rollouts,
        watch_gatekeeper_events,
        poll_gatekeeper_constraints,
    ]
    for func in watcher_funcs:
        t = threading.Thread(target=func, daemon=True, name=func.__name__)
        t.start()
        _watcher_threads.append(t)

    yield

    _stop_event.set()
    for t in _watcher_threads:
        t.join(timeout=10)


# --- App ---

app = FastAPI(
    title="Teams API",
    description="A simple API for team leads to create and manage teams",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Pydantic models ---

class TeamCreate(BaseModel):
    name: str

    @validator("name")
    def name_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("name must not be empty")
        return v.strip()


class Team(BaseModel):
    id: str
    name: str
    created_at: datetime


class EventLink(BaseModel):
    label: str
    url: str


class Event(BaseModel):
    id: str
    team_id: str
    event_type: str
    severity: str
    resource: str
    namespace: str
    message: str
    timestamp: datetime
    links: List[EventLink]


class EventCreate(BaseModel):
    event_type: str
    severity: str
    resource: str
    namespace: str
    message: str
    links: List[EventLink] = []


# --- Team CRUD endpoints (task 2.2) ---

@app.get("/")
async def root():
    return {"message": "Teams API is running"}


@app.post("/teams", response_model=Team, status_code=201)
async def create_team(team: TeamCreate):
    team_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM teams WHERE LOWER(name) = LOWER(?)", (team.name,)
        )
        if await cursor.fetchone():
            raise HTTPException(status_code=400, detail="Team name already exists")
        await db.execute(
            "INSERT INTO teams (id, name, created_at) VALUES (?, ?, ?)",
            (team_id, team.name, created_at),
        )
        await db.commit()
    return Team(id=team_id, name=team.name, created_at=created_at)


@app.get("/teams", response_model=List[Team])
async def get_teams():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, name, created_at FROM teams")
        rows = await cursor.fetchall()
    return [Team(id=row["id"], name=row["name"], created_at=row["created_at"]) for row in rows]


@app.get("/teams/{team_id}", response_model=Team)
async def get_team(team_id: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, name, created_at FROM teams WHERE id = ?", (team_id,)
        )
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Team not found")
    return Team(id=row["id"], name=row["name"], created_at=row["created_at"])


@app.delete("/teams/{team_id}")
async def delete_team(team_id: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT name FROM teams WHERE id = ?", (team_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Team not found")
        await db.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        await db.commit()
    return {"message": f"Team '{row['name']}' deleted successfully"}


@app.get("/health")
async def health_check():
    """Health check (task 2.4): reads teams_count from the database."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM teams")
        row = await cursor.fetchone()
        count = row[0] if row else 0
    return {"status": "healthy", "teams_count": count}


# --- Events API endpoint (tasks 8.1, 8.2) ---

@app.get("/teams/{team_id}/events", response_model=List[Event])
async def get_team_events(
    team_id: str,
    limit: int = Query(default=50, ge=1, le=1000),
    since: Optional[str] = Query(default=None),
):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            "SELECT id FROM teams WHERE id = ?", (team_id,)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Team not found")

        if since:
            cursor = await db.execute(
                "SELECT * FROM events WHERE team_id = ? AND timestamp > ?"
                " ORDER BY timestamp DESC LIMIT ?",
                (team_id, since, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM events WHERE team_id = ? ORDER BY timestamp DESC LIMIT ?",
                (team_id, limit),
            )
        rows = await cursor.fetchall()

    return [
        Event(
            id=row["id"],
            team_id=row["team_id"],
            event_type=row["event_type"],
            severity=row["severity"],
            resource=row["resource"],
            namespace=row["namespace"],
            message=row["message"],
            timestamp=row["timestamp"],
            links=json.loads(row["links"]),
        )
        for row in rows
    ]


@app.post("/teams/{team_id}/events", response_model=Event, status_code=201)
async def create_team_event(team_id: str, event: EventCreate):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM teams WHERE id = ?", (team_id,)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Team not found")

        event_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        links_json = json.dumps([{"label": l.label, "url": l.url} for l in event.links])
        await db.execute(
            "INSERT INTO events (id, team_id, event_type, severity, resource, namespace, message, timestamp, links)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, team_id, event.event_type, event.severity, event.resource,
             event.namespace, event.message, timestamp, links_json),
        )
        await db.commit()

    return Event(
        id=event_id,
        team_id=team_id,
        event_type=event.event_type,
        severity=event.severity,
        resource=event.resource,
        namespace=event.namespace,
        message=event.message,
        timestamp=timestamp,
        links=event.links,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
