import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite
import jwt
import requests
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from pydantic import BaseModel, validator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration (task 1.2) ---
DATABASE_PATH = os.getenv("DATABASE_PATH", "/data/teams.db")
GRAFANA_BASE_URL = os.getenv("GRAFANA_BASE_URL", "")
GRAFANA_DASHBOARD_PATH = os.getenv("GRAFANA_DASHBOARD_PATH", "")
ARGO_ROLLOUTS_BASE_URL = os.getenv("ARGO_ROLLOUTS_BASE_URL", "")

# --- Auth configuration ---
# JWKS is fetched from the in-cluster Keycloak service; the issuer is validated
# against the externally-facing hostname (KC_HOSTNAME), since that's what Keycloak
# stamps into the "iss" claim regardless of which URL was used to reach it.
KEYCLOAK_JWKS_URL = os.getenv(
    "KEYCLOAK_JWKS_URL",
    "http://keycloak-service.keycloak.svc.cluster.local:8080/realms/teams/protocol/openid-connect/certs",
)
KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER", "http://platform-auth.localhost:8080/realms/teams")
WRITE_ROLES = {"team-leader", "admin"}

_bearer_scheme = HTTPBearer(auto_error=False)
_jwks_cache: Dict[str, Any] = {}


def _get_signing_key(kid: str):
    """Fetch (and cache) the JWKS from Keycloak, returning the public key for `kid`."""
    if kid not in _jwks_cache:
        try:
            resp = requests.get(KEYCLOAK_JWKS_URL, timeout=5)
            resp.raise_for_status()
            for jwk in resp.json().get("keys", []):
                _jwks_cache[jwk["kid"]] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        except requests.exceptions.RequestException as e:
            logger.error(f"Could not fetch JWKS from Keycloak: {e}")
            raise HTTPException(status_code=503, detail="Auth service unavailable")
    if kid not in _jwks_cache:
        raise HTTPException(status_code=401, detail="Unknown signing key")
    return _jwks_cache[kid]


def require_write_access(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
):
    """Dependency for mutating routes: requires a valid Keycloak token with the
    'team-leader' or 'admin' realm role. Read-only routes stay anonymous."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = credentials.credentials
    try:
        header = jwt.get_unverified_header(token)
        key = _get_signing_key(header["kid"])
        payload = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            issuer=KEYCLOAK_ISSUER,
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    roles = set(payload.get("realm_access", {}).get("roles", []))
    if not roles & WRITE_ROLES:
        raise HTTPException(status_code=403, detail="team-leader or admin role required")

    return payload

_stop_event = threading.Event()
_watcher_threads: list = []

# Gatekeeper exposes no per-constraint label on its own metrics (only allow/deny
# totals), so we re-expose the per-constraint detail we already parse for the
# Team Event Feed. These are two different kinds of signal, so two different
# metric primitives:
#  - admission denials are discrete "this happened" events (like Falco alerts)
#    -> Counter, incremented once per FailedAdmission event.
#  - audit violations are a re-observed snapshot of currently-broken resources,
#    re-checked every 15s regardless of whether anything new happened
#    -> Gauge, set to the current count each poll cycle (not incremented).
GATEKEEPER_ADMISSION_DENIALS = Counter(
    "teams_api_gatekeeper_admission_denials_total",
    "Gatekeeper admission-time denials observed, by constraint",
    ["constraint", "namespace"],
)
GATEKEEPER_ACTIVE_VIOLATIONS = Gauge(
    "teams_api_gatekeeper_active_violations",
    "Currently active Gatekeeper audit violations, by constraint",
    ["constraint", "kind", "namespace"],
)


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

    # Persists across reconnects (the for-loop below restarts every ~60s on
    # timeout, and a watch with no starting resourceVersion replays every
    # currently-existing Event as a synthetic ADDED). Without this, every
    # reconnect would re-count every still-unexpired FailedAdmission event.
    seen_event_versions: Dict[str, str] = {}

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

                event_uid = k8s_event.metadata.uid
                event_rv = k8s_event.metadata.resource_version
                if seen_event_versions.get(event_uid) == event_rv:
                    continue
                seen_event_versions[event_uid] = event_rv

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

                # Admission-deny events emitted with --emit-admission-events look like
                # "...denied request, Resource Namespace: <ns>, Constraint: <name>, Message: ..."
                # (one FailedAdmission event per violated constraint).
                constraint_match = re.search(r"Constraint:\s*([^\s,]+)", message)
                if constraint_match:
                    GATEKEEPER_ADMISSION_DENIALS.labels(
                        constraint=constraint_match.group(1), namespace=namespace
                    ).inc()

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
            # Snapshot of current violation counts this poll cycle, keyed by
            # (constraint, kind, namespace) -> count. The Gauge is repopulated
            # from this each cycle so it reflects "currently broken", not
            # "how many times we've polled while broken".
            active_counts: Dict[tuple, int] = {}

            for crd in constraint_crds:
                group = crd.spec.group
                version = crd.spec.versions[0].name if crd.spec.versions else "v1beta1"
                plural = crd.spec.names.plural
                constraint_name = crd.metadata.name

                try:
                    constraints = custom_api.list_cluster_custom_object(
                        group=group, version=version, plural=plural
                    )
                    kind = crd.spec.names.kind
                    for constraint in constraints.get("items", []):
                        constraint_instance_name = constraint.get("metadata", {}).get("name", constraint_name)
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

                            gauge_key = (constraint_instance_name, kind, namespace)
                            active_counts[gauge_key] = active_counts.get(gauge_key, 0) + 1

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

            # Repopulate the gauge from scratch so resolved violations drop back to 0
            # instead of the label combo staying stuck at whatever it last was.
            GATEKEEPER_ACTIVE_VIOLATIONS.clear()
            for (constraint_instance_name, kind, namespace), count in active_counts.items():
                GATEKEEPER_ACTIVE_VIOLATIONS.labels(
                    constraint=constraint_instance_name, kind=kind, namespace=namespace
                ).set(count)

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
    count: int = 1


class TeamDeployment(BaseModel):
    workload_type: str = "deployment"
    name: str
    namespace: str
    replicas: int
    available_replicas: int
    updated_replicas: int
    stable_replicas: Optional[int] = None
    rollout_phase: Optional[str] = None
    rollout_step: Optional[str] = None
    created_at: Optional[datetime] = None


class PolicyViolation(BaseModel):
    constraint: str
    kind: str
    namespace: str
    resource: str
    message: str


class TeamPolicyStatus(BaseModel):
    in_violation: bool
    checked_at: datetime
    violations: List[PolicyViolation]


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
async def create_team(team: TeamCreate, _claims: dict = Depends(require_write_access)):
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
async def delete_team(team_id: str, _claims: dict = Depends(require_write_access)):
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


@app.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint: exposes per-constraint violation counts
    (Gatekeeper's own metrics have no per-rule label, see
    GATEKEEPER_ADMISSION_DENIALS and GATEKEEPER_ACTIVE_VIOLATIONS)."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health_check():
    """Health check (task 2.4): reads teams_count from the database."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM teams")
        row = await cursor.fetchone()
        count = row[0] if row else 0
    return {"status": "healthy", "teams_count": count}


@app.get("/teams/{team_id}/deployments", response_model=List[TeamDeployment])
async def get_team_deployments(team_id: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM teams WHERE id = ?", (team_id,)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Team not found")

    try:
        from kubernetes import client

        _load_k8s_config()
        core_v1 = client.CoreV1Api()
        apps_v1 = client.AppsV1Api()
        custom_api = client.CustomObjectsApi()

        namespaces = core_v1.list_namespace(
            label_selector=f"teams.example.com/team-id={team_id}"
        )

        deployments: List[TeamDeployment] = []
        for ns in namespaces.items:
            namespace = ns.metadata.name
            deployment_list = apps_v1.list_namespaced_deployment(namespace=namespace)

            for deployment in deployment_list.items:
                spec_replicas = deployment.spec.replicas or 0
                status = deployment.status
                available_replicas = status.available_replicas or 0
                updated_replicas = status.updated_replicas or 0

                # "Running" means at least one replica is currently available.
                if available_replicas < 1:
                    continue

                deployments.append(
                    TeamDeployment(
                        workload_type="deployment",
                        name=deployment.metadata.name,
                        namespace=namespace,
                        replicas=spec_replicas,
                        available_replicas=available_replicas,
                        updated_replicas=updated_replicas,
                        stable_replicas=max(spec_replicas - updated_replicas, 0),
                        created_at=deployment.metadata.creation_timestamp,
                    )
                )

            # Include Argo Rollouts as deployment-like cards when the CRD exists.
            try:
                rollouts = custom_api.list_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="rollouts",
                )
                for rollout in rollouts.get("items", []):
                    status = rollout.get("status", {})
                    spec = rollout.get("spec", {})

                    replicas = int(spec.get("replicas", 1) or 0)
                    available_replicas = int(status.get("availableReplicas", 0) or 0)
                    updated_replicas = int(status.get("updatedReplicas", 0) or 0)
                    status_replicas = int(status.get("replicas", replicas) or 0)
                    phase = (status.get("phase") or "").lower()
                    stable_replicas = max(status_replicas - updated_replicas, 0)

                    canary_steps = (spec.get("strategy", {})
                                      .get("canary", {})
                                      .get("steps", []))
                    current_step_index = int(status.get("currentStepIndex", 0) or 0)
                    step_total = len(canary_steps)
                    step_display = None
                    if step_total > 0:
                        # Convert from 0-based index to human-readable step number.
                        step_display = f"{min(current_step_index + 1, step_total)}/{step_total}"

                    # Treat healthy/progressing rollouts with available pods as running.
                    if available_replicas < 1 and phase not in {"healthy", "progressing"}:
                        continue

                    created_at = rollout.get("metadata", {}).get("creationTimestamp")
                    deployments.append(
                        TeamDeployment(
                            workload_type="rollout",
                            name=rollout.get("metadata", {}).get("name", "unknown"),
                            namespace=namespace,
                            replicas=replicas,
                            available_replicas=available_replicas,
                            updated_replicas=updated_replicas,
                            stable_replicas=stable_replicas,
                            rollout_phase=phase or None,
                            rollout_step=step_display,
                            created_at=created_at,
                        )
                    )
            except Exception as rollout_err:
                logger.debug(f"Unable to list rollouts in namespace {namespace}: {rollout_err}")

        deployments.sort(
            key=lambda item: (item.namespace.lower(), item.name.lower())
        )
        return deployments
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Unable to list deployments for team {team_id}: {e}")
        return []


@app.get("/teams/{team_id}/policy-status", response_model=TeamPolicyStatus)
async def get_team_policy_status(team_id: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM teams WHERE id = ?", (team_id,)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Team not found")

    try:
        from kubernetes import client

        _load_k8s_config()
        core_v1 = client.CoreV1Api()
        custom_api = client.CustomObjectsApi()
        ext_api = client.ApiextensionsV1Api()

        namespaces = core_v1.list_namespace(
            label_selector=f"teams.example.com/team-id={team_id}"
        )
        team_namespaces = {ns.metadata.name for ns in namespaces.items if ns.metadata and ns.metadata.name}

        if not team_namespaces:
            return TeamPolicyStatus(
                in_violation=False,
                checked_at=datetime.now(timezone.utc),
                violations=[],
            )

        violations: List[PolicyViolation] = []

        crds = ext_api.list_custom_resource_definition()
        constraint_crds = [
            crd for crd in crds.items
            if "constraints.gatekeeper.sh" in (crd.spec.group or "")
        ]

        for crd in constraint_crds:
            group = crd.spec.group
            served_versions = [v.name for v in (crd.spec.versions or []) if getattr(v, "served", False)]
            version = served_versions[0] if served_versions else "v1beta1"
            plural = crd.spec.names.plural
            kind = crd.spec.names.kind

            try:
                constraints = custom_api.list_cluster_custom_object(
                    group=group, version=version, plural=plural
                )
            except Exception as e:
                logger.debug(f"Unable to list constraint objects for {plural}: {e}")
                continue

            for constraint in constraints.get("items", []):
                constraint_name = constraint.get("metadata", {}).get("name", "unknown")
                for violation in (constraint.get("status", {}).get("violations") or []):
                    namespace = violation.get("namespace", "")
                    if namespace not in team_namespaces:
                        continue
                    violations.append(
                        PolicyViolation(
                            constraint=constraint_name,
                            kind=kind,
                            namespace=namespace,
                            resource=violation.get("name", "unknown"),
                            message=violation.get("message", "Gatekeeper policy violation"),
                        )
                    )

        return TeamPolicyStatus(
            in_violation=len(violations) > 0,
            checked_at=datetime.now(timezone.utc),
            violations=violations,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Unable to read policy status for team {team_id}: {e}")
        return TeamPolicyStatus(
            in_violation=False,
            checked_at=datetime.now(timezone.utc),
            violations=[],
        )


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
                """
                SELECT MIN(id) as id, team_id, event_type, severity, resource, namespace,
                       message, MAX(timestamp) as timestamp,
                       links, COUNT(*) as count
                FROM events
                WHERE team_id = ? AND timestamp > ?
                GROUP BY event_type, message, namespace
                ORDER BY MAX(timestamp) DESC
                LIMIT ?
                """,
                (team_id, since, limit),
            )
        else:
            cursor = await db.execute(
                """
                SELECT MIN(id) as id, team_id, event_type, severity, resource, namespace,
                       message, MAX(timestamp) as timestamp,
                       links, COUNT(*) as count
                FROM events
                WHERE team_id = ?
                GROUP BY event_type, message, namespace
                ORDER BY MAX(timestamp) DESC
                LIMIT ?
                """,
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
            count=row["count"],
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
