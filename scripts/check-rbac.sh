#!/bin/sh
set -eu

check() {
  identity=$1
  verb=$2
  resource=$3
  expected=$4
  scope=${5:-}

  if [ "$scope" = all-namespaces ]; then
    actual=$(kubectl auth can-i "$verb" "$resource" --all-namespaces --as="$identity")
  else
    actual=$(kubectl auth can-i "$verb" "$resource" --as="$identity")
  fi

  if [ "$actual" != "$expected" ]; then
    printf 'FAIL: %s %s %s: expected %s, got %s\n' \
      "$identity" "$verb" "$resource" "$expected" "$actual" >&2
    exit 1
  fi
  printf 'PASS: %s %s %s = %s\n' "$identity" "$verb" "$resource" "$actual"
}

NAMESPACE=monitoring-dev
KSM="system:serviceaccount:${NAMESPACE}:kube-state-metrics"
PROMETHEUS="system:serviceaccount:${NAMESPACE}:prometheus"

check "$KSM" list pods yes all-namespaces
check "$KSM" list deployments yes all-namespaces
check "$KSM" list nodes yes
check "$KSM" list persistentvolumes yes
check "$KSM" get secrets no all-namespaces
check "$KSM" create deployments no all-namespaces

check "$PROMETHEUS" list pods no all-namespaces
check "$PROMETHEUS" get secrets no all-namespaces
check "$PROMETHEUS" create pods no all-namespaces
