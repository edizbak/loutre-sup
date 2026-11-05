# Loutre supervision

The repository currently contains a **development-only** Kubernetes profile for the first
read-only monitoring slice. It is not production-ready and has not been applied to a cluster.

## Development profile

`k8s/dev/` contains a Kustomize stack scoped to `monitoring-dev`:

- Prometheus scrapes itself and kube-state-metrics.
- kube-state-metrics exposes only the Kubernetes object families explicitly listed in its
  arguments and read-only ClusterRole. Secret resources and labels are not enabled.
- Grafana is internal-only, has anonymous access and self-signup disabled, and provisions a
  small cluster overview dashboard.
- Grafana credentials must be provisioned as the `grafana-admin-credentials` Secret, with
  `admin-user` and `admin-password` keys, through an approved secret-management path. No
  credentials are committed here.
- Services are ClusterIP; there is no Ingress, NodePort, or LoadBalancer.
- Prometheus and Grafana use bounded `emptyDir` storage. Prometheus retains seven days. These
  are disposable development settings, not a persistence or retention recommendation.

The initial profile deliberately omits node-exporter, external route probes, alert delivery,
and production persistence. Host mounts/privileges, reachable development endpoints, storage
classes, capacity, authentication, and alert recipients need to be inventoried and decided
before those features or any production overlay are added.

The kube-state-metrics ClusterRole is cluster-scoped because it reads cluster-wide object
state. It is read-only and excludes Secrets. Review its effective permissions against the
target cluster before applying.

## Validation

Podman is the only host-side prerequisite. Run from this repository:

```sh
sh scripts/validate.sh
```

The script runs the Python contract tests, Prometheus `promtool`, a Kustomize render using a
pinned kubectl image, Kubeconform against the rendered resources, and a CLI smoke check for
the pinned kube-state-metrics image. It does not need cluster credentials and does not apply
manifests.

## Before any cluster apply

Collect the read-only server inventory requested in the project plan, review the dev profile
against the real k3s version and policies, and get explicit confirmation before deployment.
Do not apply this profile to production. In particular, do not create the Grafana Secret from
source-controlled values or expose Grafana/Prometheus publicly without the chosen
authentication and ingress configuration.
