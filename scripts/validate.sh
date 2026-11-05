#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STACK_DIR="$ROOT_DIR/k8s/dev"
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
PROMETHEUS_DIR="$TMP_DIR/prometheus"
mkdir "$PROMETHEUS_DIR"
cp "$STACK_DIR/prometheus.yml" "$STACK_DIR/prometheus-rules.yml" "$PROMETHEUS_DIR/"

podman run --rm \
  -v "$ROOT_DIR:/workspace:ro" \
  -w /workspace \
  docker.io/library/python:3.12-slim \
  sh -c 'pip install --quiet --disable-pip-version-check --root-user-action=ignore -r tests/requirements.txt && python -m unittest discover -s tests -v'

podman run --rm \
  -v "$PROMETHEUS_DIR:/etc/prometheus:ro" \
  --entrypoint /bin/promtool \
  docker.io/prom/prometheus:v3.5.0 \
  check config /etc/prometheus/prometheus.yml

podman run --rm \
  -v "$PROMETHEUS_DIR:/etc/prometheus:ro" \
  --entrypoint /bin/promtool \
  docker.io/prom/prometheus:v3.5.0 \
  check rules /etc/prometheus/prometheus-rules.yml

podman run --rm \
  -v "$STACK_DIR:/workspace:ro" \
  --entrypoint /bin/promtool \
  docker.io/prom/prometheus:v3.5.0 \
  test rules /workspace/prometheus-rules-test.yml

podman run --rm \
  -v "$ROOT_DIR/k8s/dev:/workspace:ro" \
  docker.io/rancher/kubectl:v1.35.4 \
  kustomize /workspace > "$TMP_DIR/rendered-stack.yaml"

podman run --rm -i \
  ghcr.io/yannh/kubeconform:v0.6.7 \
  -strict -summary - < "$TMP_DIR/rendered-stack.yaml"

podman run --rm \
  -v "$ROOT_DIR/k8s/dev/namespace:/workspace:ro" \
  docker.io/rancher/kubectl:v1.35.4 \
  kustomize /workspace > "$TMP_DIR/rendered-namespace.yaml"

podman run --rm -i \
  ghcr.io/yannh/kubeconform:v0.6.7 \
  -strict -summary - < "$TMP_DIR/rendered-namespace.yaml"

podman run --rm \
  -v "$ROOT_DIR/k8s/dev/bootstrap:/workspace:ro" \
  docker.io/rancher/kubectl:v1.35.4 \
  kustomize /workspace > "$TMP_DIR/rendered-bootstrap.yaml"

podman run --rm -i \
  ghcr.io/yannh/kubeconform:v0.6.7 \
  -strict -summary - < "$TMP_DIR/rendered-bootstrap.yaml"

podman run --rm \
  -v "$ROOT_DIR/gitops/argocd/dev:/workspace:ro" \
  docker.io/rancher/kubectl:v1.35.4 \
  kustomize /workspace > "$TMP_DIR/rendered-argocd-dev.yaml"

podman run --rm \
  registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.16.0 \
  --help >/dev/null

sh -n "$ROOT_DIR/scripts/inventory.sh"
sh -n "$ROOT_DIR/scripts/check-rbac.sh"
sh -n "$ROOT_DIR/scripts/validate.sh"
