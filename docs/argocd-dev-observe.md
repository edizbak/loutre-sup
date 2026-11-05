# Argo CD development observation

## Status and scope

The repository includes an Argo CD `AppProject` and `Application` at
`gitops/argocd/dev/`. The application tracks `master` and renders only `k8s/dev` into
`monitoring-dev`. This is a repository-side declaration, not an Argo CD installation or
authorization to change the cluster. Nothing in this guide has been applied to the cluster.

The Application has no automated sync policy, pruning, or self-healing. That prevents
automatic reconciliation but **does not** prevent an Argo CD user with sync permissions from
starting a manual sync. The AppProject limits the declared source, destination, and resource
kinds; it is not a security boundary for the Argo CD controller.

## Gates before installing or registering Argo CD

Do not install Argo CD or apply these resources until the operator and user have reviewed:

- The pinned Argo CD version, installation method, resource footprint, network exposure, and
  authenticated UI/API access.
- How Argo CD will reach GitHub. If repository credentials are required, configure them
  through Argo CD's repository credential mechanism; never commit them here.
- A read-only Kubernetes identity for the observed cluster, limited to `monitoring-dev` and
  the namespaced resource kinds in the AppProject. It must not be able to create, update,
  patch, or delete resources. Verify the permissions of the actual cluster registration,
  including the in-cluster registration; do not assume the AppProject narrows controller
  credentials.
- Argo CD user permissions that do not grant manual sync, update, or delete during the
  observation phase. Do not use the installation's default broad controller permissions as
  a substitute for this review.
- The state of the existing Grafana Secret and monitoring workload. Argo CD must not manage
  credentials or the separate namespace/bootstrap resources.

The intended observed workload path excludes `k8s/dev/namespace` and
`k8s/dev/bootstrap`. Those remain administrator-managed. No production path is defined.

## Render and server-side validation

The local `sh scripts/validate.sh` renders this Kustomize bundle and checks the GitOps
contracts without cluster credentials. Argo CD CRD schema validation requires an Argo CD
installation of the reviewed version; until then the local test does not claim to validate
the custom resource schemas.

After Argo CD and its CRDs are installed and the permission gates above are reviewed, render
and inspect the resources:

```sh
kubectl kustomize gitops/argocd/dev
kubectl apply --dry-run=server -k gitops/argocd/dev -n argocd
```

The server-side dry-run requires an identity allowed to inspect/create these Argo CD
resources, but does not persist them. Check that the Application source is the intended
repository and `master`, its destination is only `monitoring-dev`, and no sync policy is
present. Review the rendered resources and the current Argo CD permissions before any real
apply.

Applying the AppProject and Application is a separate, explicit cluster change. Do not
perform it until that change is approved. After approval, observe comparison status and
resource health only; do not trigger sync. An OutOfSync or Missing status is informational
and must not be "fixed" by syncing during this phase.

## Exit criteria for a future dev sync trial

A manual dev sync is a separate authorization gate. Before enabling it, design and test:

- a dedicated target-cluster identity with only the exact namespaced write permissions
  needed by `k8s/dev`;
- deny tests for cluster-scoped resources, Secrets, and namespaces outside `monitoring-dev`;
- Argo CD user access, sync authorization, audit trail, and protected Git review;
- Grafana Secret lifecycle, sync ordering, and recovery/rollback behavior.

Do not enable automated sync, pruning, self-healing, or production destinations as part of
this observation setup.
