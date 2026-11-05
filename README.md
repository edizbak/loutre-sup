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

The first action is a **server-side dry-run of the namespace only**. No real deployment has
been authorized or performed. Use a working copy of this repository on the authorized k3s
administration host. Do not run this profile in the production `default` namespace.

The manifests are split into three stages because a server-side dry-run does not persist the
Namespace it reports as `created`; submitting namespaced resources in the same dry-run can
therefore fail with `namespaces "monitoring-dev" not found`:

- `k8s/dev/namespace/`: the isolated namespace and its restricted Pod Security labels.
- `k8s/dev/bootstrap/`: cluster-wide read-only kube-state-metrics RBAC and a namespace-scoped
  `monitoring-dev-deployer` ServiceAccount/Role/RoleBinding.
- `k8s/dev/`: only the namespaced monitoring workloads, Services, ServiceAccounts and
  generated ConfigMaps.

Think of this as two separate keys for two different jobs:

| Identity | Used for | Scope |
|---|---|---|
| `sudo kubectl` administrator | Create the namespace and install/remove the bootstrap RBAC | Cluster administration |
| `monitoring-dev-deployer` kubeconfig | Dry-run, install, inspect and remove the monitoring workloads | `monitoring-dev` only |

The first identity is used only in steps 1-3 and for rollback. The second is used from step 4
onwards. A server-side dry-run never creates resources: the namespace must first be created
for real before the bootstrap or workload dry-runs can validate namespaced objects.

### 1. Validate locally and confirm the target

```sh
sh scripts/validate.sh
sudo kubectl config current-context
sudo kubectl version -o yaml
```

Stop if the selected context is not the intended cluster or the server version differs from
the inventoried k3s version. Check for existing objects before bootstrapping:

```sh
sudo kubectl get namespace monitoring-dev
sudo kubectl get deploy,sts,ds,svc,cm,sa,role,rolebinding,networkpolicy -n monitoring-dev
sudo kubectl get clusterrole monitoring-dev-kube-state-metrics
sudo kubectl get clusterrolebinding monitoring-dev-kube-state-metrics
```

`NotFound` is expected for objects not yet installed. If the namespace or RBAC names already
exist, inspect their ownership and contents; do not overwrite or delete them as part of this
runbook.

### 2. Validate and create the namespace

Run the first dry-run with the administrative identity:

```sh
sudo kubectl apply --dry-run=server -k k8s/dev/namespace
```

Review the complete output and stop on any error. This only validates the Namespace; it does
not persist it. Obtain explicit approval before creating it for real:

```sh
sudo kubectl apply -k k8s/dev/namespace
```

### 3. Validate and apply the RBAC bootstrap

The namespace must now exist. With the administrative identity, validate the cluster-scoped
RBAC and the namespaced deployer account:

```sh
sudo kubectl apply --dry-run=server -k k8s/dev/bootstrap
```

Review the complete output. It must not grant write access to kube-state-metrics, nor grant
the deployer access to cluster-scoped resources or direct Secret API access. The deployer can
create/update workloads, so treat its credential as trusted: workloads it controls may be
able to mount namespace Secrets. Obtain explicit approval before applying the bootstrap:

```sh
sudo kubectl apply -k k8s/dev/bootstrap
```

This creates `monitoring-dev-kube-state-metrics` read-only cluster RBAC, plus the namespaced
deployer account and role. The following commands create a separate kubeconfig for that
ServiceAccount using a one-hour token. Run them as your normal user from the server where
`sudo kubectl` works. They copy only the API server address and CA certificate from the
administrator's current context; the administrator certificate/key is not copied. The token
is stored in the kubeconfig, so protect that file and never paste its contents into chat.

```sh
umask 077
mkdir -p "$HOME/.kube"
DEPLOYER_KUBECONFIG="$HOME/.kube/monitoring-dev.kubeconfig"
test ! -e "$DEPLOYER_KUBECONFIG" || {
  echo "Refusing to overwrite $DEPLOYER_KUBECONFIG" >&2
  exit 1
}
CA_FILE=$(mktemp)
trap 'rm -f "$CA_FILE"' EXIT

CLUSTER_NAME=$(sudo kubectl config view --minify -o jsonpath='{.clusters[0].name}')
API_SERVER=$(sudo kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
CA_DATA=$(sudo kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
test -n "$CLUSTER_NAME" && test -n "$API_SERVER" && test -n "$CA_DATA" || {
  echo "Could not read cluster endpoint and CA from the administrator's current context" >&2
  exit 1
}
printf '%s' "$CA_DATA" | base64 --decode > "$CA_FILE"

TOKEN=$(sudo kubectl create token monitoring-dev-deployer \
  -n monitoring-dev --duration=1h)
KUBECONFIG="$DEPLOYER_KUBECONFIG" kubectl config set-cluster "$CLUSTER_NAME" \
  --server="$API_SERVER" \
  --certificate-authority="$CA_FILE" \
  --embed-certs=true
KUBECONFIG="$DEPLOYER_KUBECONFIG" kubectl config set-credentials monitoring-dev-deployer \
  --token="$TOKEN"
unset TOKEN
KUBECONFIG="$DEPLOYER_KUBECONFIG" kubectl config set-context monitoring-dev \
  --cluster="$CLUSTER_NAME" \
  --user=monitoring-dev-deployer \
  --namespace=monitoring-dev
KUBECONFIG="$DEPLOYER_KUBECONFIG" kubectl config use-context monitoring-dev
chmod 600 "$DEPLOYER_KUBECONFIG"
export KUBECONFIG="$DEPLOYER_KUBECONFIG"
kubectl config current-context
```

The last command should print `monitoring-dev`. Now check the permissions using this
kubeconfig. These should be allowed:

```sh
kubectl auth can-i patch deployments -n monitoring-dev
kubectl auth can-i create configmaps -n monitoring-dev
kubectl auth can-i create pods/portforward -n monitoring-dev
```

These should be denied:

```sh
kubectl auth can-i create namespaces
kubectl auth can-i create clusterroles
kubectl auth can-i get secrets -n monitoring-dev
```

If token creation or a permission check fails, stop and send the error (not the token).
Tokens expire after one hour; generate a replacement and update the dedicated kubeconfig
when needed. The deployer Role cannot create namespaces, access the RBAC API, or read Secrets
directly. However, because it can change Deployments and select ServiceAccounts in the
namespace, its holder must be trusted with kube-state-metrics' read-only cluster permissions
and with Secrets that workloads can mount. This is not a multi-tenant isolation boundary for
untrusted users.

### 4. Dry-run and deploy the namespaced stack

First resolve both deployment gates:

1. **In-cluster access:** all Services are ClusterIP, but the inventory found no
   NetworkPolicies, and ClusterIP alone does not restrict which pods can connect. Confirm
   whether the dev profile may initially be reachable from other cluster workloads. If not,
   first add and test a NetworkPolicy against the cluster's actual CNI and intended flows.
2. **Grafana credential:** provision `grafana-admin-credentials` in `monitoring-dev` through
   an approved secret-management path, with keys `admin-user` and `admin-password`. Never
   put the values in Git, command transcripts, or chat. Confirm presence without reading the
   Secret data:

   ```sh
   sudo kubectl get secret grafana-admin-credentials -n monitoring-dev -o name
   ```

With the restricted kubeconfig active (`KUBECONFIG` points to
`~/.kube/monitoring-dev.kubeconfig`), run the server-side dry-run and return the complete
output for review:

```sh
kubectl apply --dry-run=server -k k8s/dev
```

Stop on any schema, admission, authorization, or Pod Security error. This check does not
verify scheduling, image pulls, or referenced Secrets. After reviewing the output and both
deployment gates, obtain explicit approval before applying:

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

In separate terminals, start temporary port-forwards with the restricted kubeconfig:

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
effective monitoring-account permissions using `sh scripts/check-rbac.sh` with an operator
identity allowed to impersonate ServiceAccounts (typically the administrative kubeconfig,
not the deployer identity). If authorization checks are forbidden, use an appropriately
authorized operator rather than expanding the monitoring roles.

### 6. Rollback

Rollback requires the administrative identity. First inspect the namespace; the command lists
Secret names only, not their data:

```sh
sudo kubectl get all,configmap,secret,serviceaccount,role,rolebinding,networkpolicy -n monitoring-dev
```

If anything in the namespace is not part of this test stack, stop and prepare a targeted
rollback. Otherwise remove the workload stack, bootstrap RBAC, and finally the dedicated
namespace in separate steps:

```sh
sudo kubectl delete -k k8s/dev --wait=true
sudo kubectl delete -k k8s/dev/bootstrap --wait=true
sudo kubectl delete -k k8s/dev/namespace --wait=true
```

Deleting the namespace also deletes its Grafana Secret and any remaining namespaced data. The
profile uses ephemeral storage, so local dashboards/settings and TSDB data are discarded.

The current profile has no Ingress, external probe, persistent volume, or alert notification
receiver. Grafana access for this initial test is intended through a temporary operator
port-forward only. Alert rules are evaluated by Prometheus but do not notify anyone.
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
