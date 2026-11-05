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

## Development deployment runbook

The next step is a **server-side dry-run only**. No real deployment has been authorized or
performed. Use a working copy of this repository on the authorized k3s administration host.
Do not run this profile in the production `default` namespace.

### 1. Validate locally and confirm the target

```sh
sh scripts/validate.sh
kubectl config current-context
kubectl version -o yaml
```

Stop if the selected context is not the intended cluster or the server version differs from
the inventoried k3s version. Check whether this dedicated namespace is unused:

```sh
kubectl get namespace monitoring-dev
kubectl get deploy,sts,ds,svc,cm,sa,role,rolebinding,networkpolicy -n monitoring-dev
kubectl get clusterrole monitoring-dev-kube-state-metrics
kubectl get clusterrolebinding monitoring-dev-kube-state-metrics
```

`NotFound` is expected for objects not yet installed. If the namespace or cluster-scoped
RBAC names already exist, inspect their ownership and contents; do not overwrite or delete
them as part of this runbook.

### 2. Server-side dry-run (no changes persisted)

```sh
kubectl apply --dry-run=server -k k8s/dev
```

This sends the rendered objects through the API server's validation/admission path but does
not persist them. Review the complete output and stop on any schema, admission, authorization,
or Pod Security error. This check also does not verify that pods can be scheduled, images can
be pulled, or referenced Secrets exist. Return the output for review before proceeding.

### 3. Resolve the two deployment gates

Do not apply until both gates are explicitly resolved:

1. **In-cluster access:** all Services are ClusterIP, but the inventory found no
   NetworkPolicies, and ClusterIP alone does not restrict which pods can connect. Confirm
   whether the dev profile may initially be reachable from other cluster workloads. If not,
   first add and test a NetworkPolicy against the cluster's actual CNI and the intended
   Grafana/Prometheus flows.
2. **Grafana credential:** provision `grafana-admin-credentials` in `monitoring-dev` through
   an approved secret-management path, with keys `admin-user` and `admin-password`. Never
   put the values in Git, command transcripts, or chat. Confirm presence without reading the
   Secret data:

   ```sh
   kubectl get secret grafana-admin-credentials -n monitoring-dev -o name
   ```

The current profile has no Ingress, external probe, persistent volume, or alert notification
receiver. Access to Grafana for this initial test is intended through a temporary operator
port-forward only. Alert rules are evaluated by Prometheus but do not notify anyone.

### 4. Apply only after explicit approval

After review of the dry-run, in-cluster access decision, and Secret setup, obtain explicit
approval before changing the cluster. Then apply:

```sh
kubectl apply -k k8s/dev
kubectl -n monitoring-dev rollout status deployment/prometheus --timeout=180s
kubectl -n monitoring-dev rollout status deployment/kube-state-metrics --timeout=180s
kubectl -n monitoring-dev rollout status deployment/grafana --timeout=180s
kubectl -n monitoring-dev get pods,svc,endpoints
```

If any rollout fails, stop and inspect before retrying:

```sh
kubectl -n monitoring-dev get pods -o wide
kubectl -n monitoring-dev describe pods
kubectl -n monitoring-dev logs deployment/prometheus
kubectl -n monitoring-dev logs deployment/kube-state-metrics
kubectl -n monitoring-dev logs deployment/grafana
```

### 5. Verify the running profile

In separate terminals, start temporary port-forwards:

```sh
kubectl -n monitoring-dev port-forward svc/prometheus 9090:9090
kubectl -n monitoring-dev port-forward svc/grafana 3000:3000
```

While the Prometheus forward is active, verify readiness, scrape targets, and loaded rules:

```sh
curl -fsS http://127.0.0.1:9090/-/ready
curl -fsS http://127.0.0.1:9090/api/v1/targets
curl -fsS http://127.0.0.1:9090/api/v1/rules
```

Open Grafana at `http://127.0.0.1:3000`, sign in with the provisioned Secret, and check the
provisioned Cluster overview dashboard. Stop both port-forwards after the check. Verify
effective permissions using `sh scripts/check-rbac.sh`; the operator must be allowed to
impersonate ServiceAccounts. If authorization checks are forbidden, use an appropriately
authorized operator rather than expanding the monitoring roles.

### 6. Rollback

This rollback deletes the dedicated namespace and every object in it, including any Secret
provisioned there. Use it only after confirming `monitoring-dev` contains no unrelated
workloads or data:

```sh
kubectl get all,configmap,secret,serviceaccount,role,rolebinding,networkpolicy -n monitoring-dev
kubectl delete -k k8s/dev --wait=true
```

The first command lists Secret names only, not their data. If anything in the namespace is
not part of this test stack, stop and prepare a targeted rollback instead. The profile uses
ephemeral storage, so its local dashboards/settings and TSDB data are discarded on rollback.

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
