# Capstone Demo Script (preliminary)

Pre-flight checks are skipped on camera — cluster, Gatekeeper, Argo Rollouts, Keycloak,
Teams API/UI, and Grafana are already running before recording starts.

## 1. Simulated build pipeline
- Run `build.sh` for a color variant (e.g. `purple`): builds EmojiApi, runs the .NET test
  suite + coverage, builds the Docker image, loads it into kind.
- Call out that the recorded coverage % is later checked by the Gatekeeper quality gate.

## 2. Team creation → propagation
- `teams_cli.py login` — opens a browser for device login against Keycloak (`teams-cli`
  client); show the device code prompt and browser sign-in.
- `teams_cli.py whoami` — confirm the identity/roles picked up from the token.
- `teams_cli.py create "Platform Engineering"` — call out that this now requires the
  `team-leader`/`admin` role, enforced by `teams-api` (a plain unauthenticated `curl
  POST /teams` would get a 401/403 here — worth showing once for contrast).
- Show the request hit `teams-api` → operator picks it up → namespace
  `team-platform-engineering` appears via `kubectl get ns`.
- Call out: CLI → API → Operator → namespace is the same propagation chain built earlier
  in the course, now with the create call actually authenticated end-to-end.

## 3. Switch to a team member login
- Log out of the admin/platform view in the Teams UI.
- Log in as `teamlead1` via Keycloak.
- Show the scoped view (only their team, `team-leader` role).
- (CLI equivalent, if wanted for contrast) `teams_cli.py logout` then `teams_cli.py login`
  as `teamlead1`, `teams_cli.py whoami` to confirm the switch.

## 4. Deploy EmojiAPI as that team
- Run `deploy.sh --team "Platform Engineering" --color purple` (plain `Deployment`) →
  **denied** by the `K8sRequireArgoRollout` constraint. Show the kubectl error live.
- Re-run with `--good` (Argo `Rollout`, canary) → succeeds. Hit `/health` and the emoji
  endpoint to prove it's live.

## 5. Prove Argo Rollouts is real
- Deploy `--color green`, then `--color orange` as new revisions.
- Show `kubectl argo rollouts get rollout emoji-api --watch` progressing through the
  canary steps (50% → pause → promote).
- Open the Argo Rollouts dashboard: both color pods running side-by-side mid-canary, then
  promote and show the old color scale down.

## 6. Quality gate violation (bonus)
- Deploy `--color red` (deliberately low-coverage demo SHA) → blocked by the
  `CodeCoverageSimple` constraint. Show the denial message referencing commit SHA and
  coverage %.

## 7. Platform team view: Grafana violations dashboard
- Switch to the security/Gatekeeper Grafana dashboard.
- Show CVE/quality/Argo-rollout denials logged as metrics — platform team sees violations
  across all teams without touching kubectl.

## 8. Wrap-up
- Recap: policy-as-code enforcing Argo Rollouts, quality gates, and CVE scanning;
  propagation from CLI to namespace; canary color-based verification; Grafana visibility
  for platform ops.

---

**Open items before this script is final:**
- Real (non-simulated) build pipeline is postponed; step 1 stays as the "simulated"
  version for now.
- CLI login / API auth is implemented (`teams-cli` device flow, `POST/DELETE /teams`
  protected). Needs a live run-through once Keycloak has picked up the realm/client
  change to confirm the flow works end-to-end before recording.
