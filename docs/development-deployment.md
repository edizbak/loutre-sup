# Development deployment runbook

This runbook deploys only the development monitoring profile into `monitoring-dev`. It is not
a production procedure. Do not apply it to the application `default` namespace.

## Deployment stages and identities

The manifests are split into three Kustomize targets:

| Target | Contents | Identity |
|---|---|---|
| `k8s/dev/namespace/` | Namespace and restricted Pod Security labels | Cluster administrator |
| `k8s/dev/bootstrap/` | kube-state-metrics and Metrics Server exporter ClusterRole/Bindings, plus namespace deployer Role/Binding | Cluster administrator |
| `k8s/dev/` | Monitoring Services, workloads, ServiceAccounts and ConfigMaps | `monitoring-dev-deployer` |

This split is necessary because a server-side dry-run validates objects but does not persist
them. A dry-run that submits a Namespace and namespaced objects together can report the
Namespace as "created" while failing the namespaced objects because the Namespace does not
exist yet.

Run commands as your normal sudo-capable account. Do not run this runbook or a shell block
with `sudo`; only prefix the administrator `kubectl` commands with `sudo`. `sudo -v` at the
start of a command sequence prompts for authentication; later `sudo` commands prompt again
if that cached authentication expires. The deployer kubeconfig is for workload operations,
not cluster bootstrap.

## 1. Validate and check the target cluster

Run from the root of the `loutre-sup` repository:

```sh
sudo -v
sh scripts/validate.sh
sudo kubectl config current-context
sudo kubectl version -o yaml
```

Stop if the context is not the intended cluster or the server version differs from the
inventoried k3s version. Check whether the dedicated names already exist:

```sh
sudo kubectl get namespace monitoring-dev
sudo kubectl get deploy,sts,ds,svc,cm,sa,role,rolebinding,networkpolicy -n monitoring-dev
sudo kubectl get clusterrole monitoring-dev-kube-state-metrics
sudo kubectl get clusterrolebinding monitoring-dev-kube-state-metrics
sudo kubectl get clusterrole monitoring-dev-metrics-server-exporter
sudo kubectl get clusterrolebinding monitoring-dev-metrics-server-exporter
```

`NotFound` is expected for objects not yet installed. If any names already exist, inspect
their ownership and contents; do not overwrite or delete them as part of this procedure.

## 2. Validate and create the namespace

Dry-run only the namespace:

```sh
sudo kubectl apply --dry-run=server -k k8s/dev/namespace
```

Review the output and stop on any error. After explicit approval, create it for real:

```sh
sudo kubectl apply -k k8s/dev/namespace
sudo kubectl get namespace monitoring-dev
```

## 3. Validate and install the RBAC bootstrap

The namespace must exist before this stage. Validate the bootstrap:

```sh
sudo kubectl apply --dry-run=server -k k8s/dev/bootstrap
```

Review the complete output. kube-state-metrics must have read-only cluster permissions.
The Metrics Server exporter role must only list pods and nodes in the `metrics.k8s.io` API
group; it must not grant access to core Kubernetes resources or the kubelet.
The deployer Role is limited to this namespace and does not grant direct Secret access,
cluster-scope access, or RBAC modification. The deployer can modify Deployments and select
ServiceAccounts in the namespace, so its holder must be trusted with the monitoring
permissions and Secrets that workloads can mount.

After explicit approval, install the bootstrap:

```sh
sudo kubectl apply -k k8s/dev/bootstrap
```

## 4. Create a restricted deployer kubeconfig

The following is a shell block, not a script to run with `sudo`. Paste it into the
interactive shell of your normal user on the administration host. Running the block as root
would create the kubeconfig in root's home and change its ownership.

It creates a one-hour ServiceAccount token and copies only the API server address and CA from
the current administrator context; it does not copy the administrator certificate or key.
The resulting kubeconfig contains a bearer token. Keep it private, do not paste it into chat,
and do not commit it.

```sh
sudo -v
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

The last command should print `monitoring-dev`. Verify the allowed permissions:

```sh
kubectl auth can-i patch deployments -n monitoring-dev
kubectl auth can-i create configmaps -n monitoring-dev
kubectl auth can-i create pods --subresource=portforward -n monitoring-dev
```

All three should return `yes`. These should return `no`:

```sh
kubectl auth can-i create namespaces
kubectl auth can-i create clusterroles
kubectl auth can-i get secrets -n monitoring-dev
```

If the port-forward check returns `no`, re-apply the bootstrap with the administrator and
repeat the check:

```sh
sudo kubectl apply -k k8s/dev/bootstrap
kubectl auth can-i create pods --subresource=portforward -n monitoring-dev
```

The expected result is `yes`. RBAC changes do not require a new token; do not generate a new
kubeconfig just for this permission update. If token creation or permission checks fail, stop
and report the error without sharing the token. Because the deployer can change workloads and
choose ServiceAccounts, it is not suitable for untrusted or multi-tenant users.

### Renew an expired deployer token

The ServiceAccount token expires after one hour. Renew only the credential in the existing
restricted kubeconfig; do not recreate or overwrite that file. Run this in the normal
interactive shell with shell tracing disabled:

```sh
sudo -v
DEPLOYER_KUBECONFIG="$HOME/.kube/monitoring-dev.kubeconfig"
TOKEN=$(sudo kubectl create token monitoring-dev-deployer \
  -n monitoring-dev --duration=1h)
KUBECONFIG="$DEPLOYER_KUBECONFIG" kubectl config set-credentials \
  monitoring-dev-deployer --token="$TOKEN"
unset TOKEN

export KUBECONFIG="$DEPLOYER_KUBECONFIG"
kubectl config current-context
kubectl auth can-i patch deployments -n monitoring-dev
```

The context should remain `monitoring-dev` and the permission check should return `yes`.
Do not print, paste, or log the token. If renewal or the permission check fails, stop and
investigate before retrying cluster operations.

## 5. Create the Grafana admin Secret

Follow [Grafana admin credential setup](./grafana-admin-secret.md). This uses the
administrator identity because the deployer Role intentionally cannot create or read Secrets.
The Secret must exist before Grafana starts.

## 6. Dry-run and deploy the namespaced stack

Before deploying, resolve this gate:

**Grafana credentials:** confirm the Secret exists by name only, without displaying data:

```sh
sudo kubectl get secret grafana-admin-credentials -n monitoring-dev -o name
```

For this development-only profile, access to the ClusterIP Services from other pods in the
cluster is accepted; no NetworkPolicy is required to proceed. This is not an acceptable
default for production. Before deploying a production monitoring stack, design and validate
network isolation so a compromised unrelated workload cannot reach or compromise the
monitoring services. Base that design on the cluster's actual CNI and test the required
Prometheus, Grafana, and exporter flows.

With the restricted kubeconfig active, run the server-side dry-run and review the complete
output:

```sh
kubectl apply --dry-run=server -k k8s/dev
```

Stop on schema, admission, authorization, or Pod Security errors. This check does not verify
scheduling, image pulls, or whether the Grafana Secret exists. After review and explicit
approval, apply the stack:

```sh
kubectl apply -k k8s/dev
kubectl -n monitoring-dev rollout status deployment/prometheus --timeout=180s
kubectl -n monitoring-dev rollout status deployment/kube-state-metrics --timeout=180s
kubectl -n monitoring-dev rollout status deployment/grafana --timeout=360s
kubectl -n monitoring-dev get pods,svc,endpoints
```

Grafana's startup probe allows up to five minutes for its first database migrations.
kube-state-metrics readiness checks its metrics endpoint on port 8080, while liveness
checks that the metrics port accepts connections.

If a rollout fails, stop and inspect before retrying:

```sh
kubectl -n monitoring-dev get pods -o wide
kubectl -n monitoring-dev describe pods
kubectl -n monitoring-dev logs deployment/prometheus
kubectl -n monitoring-dev logs deployment/kube-state-metrics
kubectl -n monitoring-dev logs deployment/grafana
```

## 7. Verify the running profile

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

Open Grafana at `http://127.0.0.1:3000`, sign in with the Secret credentials, and check the
provisioned Cluster overview dashboard. Stop both port-forwards when done. To verify the
monitoring ServiceAccount permissions, run `sh scripts/check-rbac.sh` with an operator
identity allowed to impersonate ServiceAccounts (typically the administrator kubeconfig).
Do not broaden monitoring roles if impersonation is forbidden.

## 8. Rollback

Rollback requires the administrator identity. Inspect the namespace first; this lists Secret
names but not their data:

```sh
sudo kubectl get all,configmap,secret,serviceaccount,role,rolebinding,networkpolicy -n monitoring-dev
```

If anything is unrelated to this stack, stop and plan a targeted rollback. Otherwise delete
the workloads, bootstrap, then namespace separately:

```sh
sudo kubectl delete -k k8s/dev --wait=true
sudo kubectl delete -k k8s/dev/bootstrap --wait=true
sudo kubectl delete -k k8s/dev/namespace --wait=true
```

Deleting the namespace also deletes its Grafana Secret and remaining namespaced data. The
profile uses ephemeral storage, so its dashboards/settings and TSDB data are discarded.

This profile has no Ingress, external probe, persistent volume, or alert notification
receiver. Grafana is intended to be accessed only via a temporary operator port-forward.
