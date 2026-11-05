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
- Prometheus and Grafana use bounded `emptyDir` storage. Prometheus retains at most seven days
  and 750 MB of TSDB blocks. These
  are disposable development settings, not a persistence or retention recommendation.
- Prometheus rules cover an unavailable scrape target, unavailable deployment replicas, a
  non-Ready node, and missing node metrics. Rules are evaluated by promtool tests, but there
  is no Alertmanager/notification receiver yet.

The initial profile deliberately omits node-exporter, external route probes, alert delivery,
and production persistence. Host mounts/privileges, reachable development endpoints, storage
retention/backup, authentication, and alert recipients need further decisions before those
features or any production overlay are added.

The server inventory (2026-11-05) found a single-node k3s v1.35.4 cluster, 4 vCPU, about 8 GiB
RAM, and about 18 GiB free on the 59 GiB root filesystem. Metrics Server is already installed
and serving `metrics.k8s.io`; do not install a duplicate for `kubectl top`. The default
`local-path` storage is node-local, non-expandable, and has `Delete` reclaim policy. These
observations are a snapshot, not an availability or capacity guarantee. There are no current
NetworkPolicies, ResourceQuotas, or LimitRanges. The node had old completed/failed/evicted pod
records even though current application deployments were ready; alert rules should avoid
treating old terminal pods as current service failures. The single-node cluster has no
monitoring high availability and cannot report while the host is down.

The kube-state-metrics ClusterRole is cluster-scoped because it reads cluster-wide object
state. It is read-only and excludes Secrets. Review its effective permissions against the
target cluster before applying.

The dashboard compares declared pod CPU/memory requests with node allocatable capacity.
The current Prometheus profile does not ingest live CPU/memory usage from the existing
Metrics Server API. Kube-state-metrics state/capacity series must not be mistaken for actual
resource utilization. Decide whether a narrowly scoped metrics-server integration belongs
in a later milestone.

## Validation

Podman is the only host-side prerequisite. Run from this repository:

```sh
sh scripts/validate.sh
```

The script runs the Python contract tests, Prometheus `promtool`, a Kustomize render using a
kubectl image matching the inventoried cluster version, Kubeconform against the rendered
resources, and a CLI smoke check for the pinned kube-state-metrics image. It does not need
cluster credentials and does not apply manifests.

After an explicitly approved dev installation, an operator authorized to impersonate
ServiceAccounts can run `sh scripts/check-rbac.sh`. It uses only `kubectl auth can-i` to
confirm kube-state-metrics read access and to confirm that neither it nor Prometheus can read
Secrets or write Kubernetes resources. It does not apply or change cluster resources.

## Before any cluster apply

The read-only server inventory is recorded in `STACK-PLAN.md`. Recheck the cluster version
and policies before deployment, and get explicit confirmation before any apply.
Do not apply this profile to production. In particular, do not create the Grafana Secret from
source-controlled values or expose Grafana/Prometheus publicly without the chosen
authentication and ingress configuration.

ClusterIP Services have no public listener, but they are not an authorization boundary between
pods. The inventoried cluster currently has no NetworkPolicies, so confirm which in-cluster
workloads may reach these endpoints before installing the profile.

To collect the inventory on the k3s server, run `sh scripts/inventory.sh` from this repository
on a machine with authorized `kubectl` access. It only issues `kubectl get`, `kubectl top`,
`kubectl version` and local host-information commands; failed checks are reported and the
script continues. Review the output before sharing it and redact public addresses or identifiers
as needed. Never share Secrets, kubeconfigs or tokens.
