# CI, versioning and GitOps proposal

## Status and scope

This document describes the CI/versioning policy and the gradual GitOps path for `loutre-sup`.
The repository now has a GitHub remote and the first validation workflow is in place;
the proposal does not install Argo CD, change the Anima workflow, or deploy anything to the
cluster.

The goals are to make every change reviewable and reproducible, continuously validate
manifests without cluster credentials, and eventually separate validation from cluster
reconciliation. The development stack remains the first adoption target; production is a
later, separately approved environment.

## What exists today

- `scripts/validate.sh` runs 29 contract tests, Prometheus configuration/rule validation,
  Kustomize rendering and Kubeconform schema checks using Podman.
- `.github/workflows/validate.yml` runs the existing validation script on pull requests and
  pushes to `master`. It has read-only repository permission and no cluster credentials.
- `gitops/argocd/dev/` declares a restricted AppProject and a `master`-tracking Application
  for `k8s/dev`. It has no automated sync policy; neither Argo CD nor these resources have
  been installed/applied, and they do not prevent manual sync by an authorized Argo CD user.
- Manifests pin component image versions, but there is no repository release/version policy.
- Anima's `.github/workflows/ci.yml` tests on PRs and `main`, publishes API/frontend/sidecar
  images tagged `latest`, and can directly apply manifests with a GitHub `KUBECONFIG` secret.
  That workflow is a useful existing reference, but this proposal does not change it.

## Recommended delivery model

Keep three different versions/concepts distinct:

1. **Git commit** identifies an exact source change. Use small, coherent commits with
   Conventional Commit subjects (`feat:`, `fix:`, `docs:`, `ci:`). The assistant can commit
   after a meaningful, validated milestone instead of requiring the user to commit every
   round of edits. Group changes into reviewable units; ask before pushing or force-pushing.
   A later squash may combine exploratory commits.
2. **Stack release tag** identifies a reviewed source snapshot, e.g. `v0.1.0`. Initially,
   create these manually after CI passes and the change is accepted. Use SemVer conservatively:
   while the stack is experimental, use `0.y.z`; increment `y` for a backward-incompatible
   manifest/operational contract and `z` for compatible fixes. A Git tag is a release marker,
   not an instruction to deploy.
3. **Container image identity** is an immutable registry digest, not a mutable `latest` tag.
   The current monitoring profile consumes pinned upstream images and builds no images, so
   it does not need a package/image publishing pipeline. If custom exporters or dashboards
   later require an image, build it after tests and deploy by digest.

For Anima, adopt immutable image tags/digests and release metadata as a separate project.
The existing CI's `latest` publication and optional direct cluster apply make source,
artifact and deployed state harder to correlate. Any migration to GitOps should first make
the image build reproducible and deploy manifests reference a fixed digest; do not make a
simultaneous monitoring-repo task silently alter Anima's production deploy flow.

## GitHub Actions CI for `loutre-sup`

- Run on `pull_request` to the default branch and pushes to that branch.
- Use a read-only `contents: read` token; CI needs no Kubernetes context, kubeconfig,
  Secrets, package-write permission or cluster access.
- Install/use Podman on the runner and execute the same `sh scripts/validate.sh` used
  locally. This keeps local and hosted acceptance criteria aligned.
- Fail the check on contract tests, promtool, any Kustomize render, Kubeconform and shell
  syntax errors. The contract test prevents Kubernetes Secret objects from being committed,
  but it is not a general-purpose secret scanner; adding one is a separate choice.
- Pin CI actions to reviewed immutable commit SHAs (renovate/update automation can be
  considered later); pin tool/container versions in the validation script.
- Make this check required in branch protection when repository settings are available.
- Do not deploy from pull requests. A green check means "validated", not "approved to apply".

The first credential-free validation workflow is now in place. Configure its successful job
as a required PR check in GitHub settings when convenient; that setting is outside the Git
repository and is not applied by the workflow itself.

The initial CI should not run integration tests against the live cluster. Server-side
dry-runs, rollout verification and secret provisioning remain explicit operator steps in
the deployment runbook. Local `validate.sh` plus the CI job can validate YAML/rendered
schemas but cannot prove admission, scheduling, registry access or runtime behavior.

## Versioning and change intake

- Use PRs as the default merge path. The test/validation check must
  pass before merge; record any manual server evidence in the PR without including secret
  values, kubeconfigs, tokens or unnecessary infrastructure identifiers.
- Require review for production-specific paths and for changes to cluster-scoped RBAC,
  credentials handling, network isolation and deployment automation.
- Keep commit subjects descriptive and consistent; Conventional Commits make later release
  notes and manual SemVer selection easier but do not by themselves guarantee a safe release.
- Tag only a reviewed commit that has passed CI. Initially create tags manually; avoid
  automatic version bumps/releases until the repository structure and release cadence are
  established.
- For a deployable snapshot, prefer a Git commit SHA or signed release tag over a branch
  name. Never use a floating branch or `latest` as the production source of truth.

## Argo CD: what it adds, and what it does not

Argo CD is a Kubernetes GitOps reconciler: it watches a Git path and compares its declared
objects with cluster state. It can report drift, show sync/health status, and apply reviewed
Git changes. It does not replace tests, review, a versioning policy, secret management,
backups, network isolation or an operator's decision to authorize a deployment.

### Suggested adoption stages

1. **CI only:** run validation on PRs and the default branch. Continue the documented,
   operator-invoked deployment procedure; do not give Actions cluster credentials.
2. **Observe-only Argo CD:** after the version, repository access, cluster read-only
   identity, controller permissions and UI/API access model are reviewed, install a pinned
   Argo CD release and apply the declarations in `gitops/argocd/dev/`. Observe diffs and
   health without sync permissions; the absent automated sync policy alone does not prevent
   a manual sync.
3. **Controlled dev sync:** constrain an Argo CD AppProject to the intended repository,
   `monitoring-dev` destination and approved namespaced kinds. Keep namespace creation,
   cluster-scoped RBAC and `k8s/dev/bootstrap` as an explicit administrator bootstrap;
   Argo CD's application controller should not receive general cluster-admin just to make
   that convenient. Grant only the API permissions needed for the namespaced workload
   resources, and test denies as well as allows.
4. **Production proposal:** only after dev drift detection and sync behavior are proven,
   design a separate production path with independent approval, namespace, credentials,
   network policy, persistence/backup and rollback. Do not point the dev Application at
   production. Automated sync/prune/self-heal should remain disabled until their recovery
   behavior and authorization are explicitly accepted.

AppProjects constrain Applications but are not, on their own, a security boundary against a
compromised Argo CD controller. The controller's Kubernetes RBAC, Argo CD user roles, Git
credentials and network reachability must also be minimized. UI/API access should be private
or protected by the eventual identity layer; no public unauthenticated Argo CD endpoint.

### Secrets with GitOps

Do not commit the Grafana Secret, base64-encoded values, or a token-bearing kubeconfig. The
current operator-created Secret is a suitable first dev path. Before production GitOps,
choose and test a secret-management integration (for example an external secrets operator
backed by an approved secret store, or a sealed/encrypted-secret workflow with managed keys).
The choice must include key ownership, rotation, backup/recovery, RBAC, and what appears in CI
logs and diffs. Argo CD must not become a path to render secret plaintext into Git or build
logs.

## Proposed gates

| Gate | Exit evidence | Cluster changes |
|---|---|---|
| A. CI design accepted | Host repo approval; CI and branch/review policy agreed | None |
| B. CI enabled | Required checks pass on a PR; no cluster credentials in CI | None |
| C. GitOps readiness | Argo CD version, network, RBAC boundary, repo auth, UI access and secret strategy reviewed | Install only after separate approval |
| D. Dev observe-only | Application reports expected diff/health with automated sync off | No workload changes by Argo |
| E. Dev sync trial | Reviewed change syncs in `monitoring-dev`; rollback and permission-deny tests pass | Dev namespace only |
| F. Production GitOps | Separate production design and explicit authorization | Out of scope until approved |

The GitHub repository now exists. Do not treat this as approval to install Argo CD or to give
CI cluster credentials; those are separate gates requiring design and explicit authorization.
