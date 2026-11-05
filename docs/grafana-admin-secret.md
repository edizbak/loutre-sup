# Grafana admin credential setup

Grafana requires a Kubernetes Secret named `grafana-admin-credentials` in `monitoring-dev`,
with the keys `admin-user` and `admin-password`. The workload reads these values on startup.
The Secret is not in Git; create it directly in the cluster using an administrator identity.
The restricted `monitoring-dev-deployer` account cannot create or read it.
Kubernetes Secret values are base64-encoded in API representations, not made safe to share;
never treat encoded output as encrypted or paste it into logs or chat.

## Before starting

- The `monitoring-dev` namespace must already exist.
- The bootstrap has been applied (this procedure uses `sudo kubectl`, not the deployer
  kubeconfig).
- Run the commands from your normal sudo-capable account, not from a root shell.
- Choose a unique, strong password and store it in your password manager.
- Do not put credentials in command arguments, a checked-in file, chat, or a terminal
  transcript. Do not enable shell tracing (`set -x`) while entering them.

## Create the Secret

Run this whole block in an interactive Bash-compatible shell. It prompts without echoing the
password, asks for confirmation, and streams the values directly to `kubectl` via standard
input. It does not write a credentials file. `kubectl create` fails rather than overwriting
an existing Secret.

```sh
(
  set -eu
  sudo -v

  printf 'Grafana admin username [admin]: '
  IFS= read -r GRAFANA_ADMIN_USER
  GRAFANA_ADMIN_USER=${GRAFANA_ADMIN_USER:-admin}

  IFS= read -r -s -p 'Grafana admin password: ' GRAFANA_ADMIN_PASSWORD
  printf '\n'
  IFS= read -r -s -p 'Confirm password: ' GRAFANA_ADMIN_PASSWORD_CONFIRM
  printf '\n'

  if [ "$GRAFANA_ADMIN_PASSWORD" != "$GRAFANA_ADMIN_PASSWORD_CONFIRM" ]; then
    echo "Passwords do not match; no Secret was created." >&2
    exit 1
  fi
  if [ -z "$GRAFANA_ADMIN_USER" ] || [ -z "$GRAFANA_ADMIN_PASSWORD" ]; then
    echo "Username and password must not be empty; no Secret was created." >&2
    exit 1
  fi

  printf 'admin-user=%s\nadmin-password=%s\n' \
    "$GRAFANA_ADMIN_USER" "$GRAFANA_ADMIN_PASSWORD" |
    sudo -n kubectl create secret generic grafana-admin-credentials \
      --from-env-file=/dev/stdin \
      -n monitoring-dev
)
```

`kubectl create` fails rather than overwriting an existing Secret. If creation fails because
the Secret already exists, access is denied, or the namespace is missing, stop and investigate
the cause. Do not delete or replace an existing Secret without confirming its owner and
planning a credential rotation.

## Verify without revealing the values

```sh
sudo kubectl get secret grafana-admin-credentials -n monitoring-dev -o name
```

This prints only the resource name. Do not use `kubectl get secret ... -o yaml` or
`--output=jsonpath` to inspect credential data, and do not send Secret output for review.

The Secret must exist before deploying Grafana. If it is created or changed after Grafana has
started, restart the Grafana Deployment so its environment variables are reloaded:

```sh
kubectl -n monitoring-dev rollout restart deployment/grafana
kubectl -n monitoring-dev rollout status deployment/grafana --timeout=180s
```

Use the restricted deployer kubeconfig for that restart. Plan any password rotation carefully:
the existing admin password is not readable from Kubernetes in plaintext, and changing the
Secret alone does not change Grafana's already initialized admin account password.
