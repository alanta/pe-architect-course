# Capstone Demo Script (preliminary)

## 0. Preparation

Pre-flight checks are skipped on camera — cluster, Gatekeeper, Argo Rollouts, Keycloak,
Teams API/UI, and Grafana are already running before recording starts.

- Purge images from Kind / Docker
- Purge coverage data for current commit
- Purhe team-cli credentials


## Intro

## 1. Team creation → propagation (must come first — deployment targets the team namespace)

Complete team creation flow using CLI and API with authentication, triggering operator and shown in 

- `teams_cli.py login` as `teamlead1` — opens a browser for device login against Keycloak
  (`teams-cli` client); show the device code prompt and browser sign-in. We stay logged in
  as `teamlead1` for the rest of the demo — no admin/team-lead context switching.
- `teams_cli.py whoami` — confirm the identity/role picked up from the token. Call out
  this is a role check, not a data-scoping check today: any authenticated
  `team-leader`/`admin` can create/delete any team; there's no per-team ownership model
  (yet) — worth naming as a natural next step, not building live.
- `teams_cli.py create "Pink"` — call out that this now requires the
  `team-leader`/`admin` role, enforced by `teams-api` (a plain unauthenticated `curl
  POST /teams` would get a 401/403 here — worth showing once for contrast). Team name is
  a color ("Pink") to keep the framing as an implementation team consuming the platform,
  not the platform team itself — deliberately distinct from the `purple`/`green`/`orange`/
  `red` emoji-color image variants used later, to avoid confusion between team name and
  image color.

## 2. Demonstrate desired state
- Switch to the Teams UI portal, log in as `teamlead1`, show the team now listed there.
- Switch to the VS Code Kubernetes extension: show the `team-pink`
  namespace appear
- Call out: CLI → API → Operator → namespace is the propagation chain built earlier in
  the course, now with the create call authenticated end-to-end.

## 3. Deploy EmojiAPI as that team (the actual capstone requirement)
- Explain demo app
- Run `deploy.sh --team "Pink" --color purple` (plain `Deployment`) →
  **denied** by the `K8sRequireArgoRollout` constraint. Show the kubectl error live.
- Show the violations in the [teams ui](http://teams-ui.localhost:8080/)
- Re-run with `--good` (Argo `Rollout`, canary) → succeeds. 
- Hit http://team-pink.localhost:8080/ to see the app running
- Show the app in the [teams ui](http://teams-ui.localhost:8080/)

## 4. Prove Argo Rollouts is real
- Hit http://team-pink.localhost:8080/ to see the app running again
- In the console deploy `--color green`, then `--color orange` as new revisions. - new color emojies should start showing
- Open the Argo Rollouts dashboard: http://localhost:3100/rollouts/rollout/team-pink/emoji-api
  Both color pods running side-by-side mid-canary, then promote and show the old color scale down.

## 5. Platform team view: Grafana violations dashboard
- Switch to the security/Gatekeeper Grafana dashboard.
- Show CVE/quality/Argo-rollout denials logged as metrics — platform team sees violations
  across all teams without touching kubectl.

## 6. Bonus round (extras beyond the requirements)
- **Simulated build pipeline**: run `build.sh` for a color variant — builds EmojiApi,
  runs the .NET test suite + coverage, builds the Docker image, loads it into kind. Call
  out the recorded coverage % feeds the Gatekeeper quality gate.
- **Quality gate violation**: deploy `--color red` (deliberately low-coverage demo SHA) →
  blocked by the `CodeCoverageSimple` constraint, denial message references commit SHA
  and coverage %.
- **CVE/NuGet scanning**: mention the extended CVE constraint checking both image and
  package-level (NuGet) CVEs.
- **SecOps runtime monitoring**: Falco + the custom Grafana security dashboard, if time
  allows.

## 6. Wrap-up
- Recap: policy-as-code enforcing Argo Rollouts, quality gates, and CVE scanning;
  authenticated propagation from CLI to namespace; canary color-based verification;
  Grafana visibility for platform ops.

