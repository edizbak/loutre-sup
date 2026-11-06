#!/bin/sh
set -eu

check() {
  identity=$1
  verb=$2
  resource=$3
  expected=$4
  scope=${5:-}

  if [ "$scope" = all-namespaces ]; then
    if actual=$(kubectl auth can-i "$verb" "$resource" \
      --all-namespaces --as="$identity"); then
      command_status=0
    else
      command_status=$?
    fi
  else
    if actual=$(kubectl auth can-i "$verb" "$resource" --as="$identity"); then
      command_status=0
    else
      command_status=$?
    fi
  fi

  case "$actual" in
    yes)
      [ "$command_status" -eq 0 ] || {
        printf 'FAIL: kubectl auth can-i %s %s exited with status %s\n' \
          "$verb" "$resource" "$command_status" >&2
        exit 1
      }
      ;;
    no)
      [ "$command_status" -eq 1 ] || {
        printf 'FAIL: kubectl auth can-i %s %s exited with status %s\n' \
          "$verb" "$resource" "$command_status" >&2
        exit 1
      }
      ;;
    *)
      printf 'FAIL: kubectl auth can-i %s %s returned unexpected output: %s\n' \
        "$verb" "$resource" "$actual" >&2
      exit 1
      ;;
  esac

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
METRICS_EXPORTER="system:serviceaccount:${NAMESPACE}:metrics-server-exporter"

check "$KSM" list pods yes all-namespaces
check "$KSM" list deployments yes all-namespaces
check "$KSM" list nodes yes
check "$KSM" list persistentvolumes yes
check "$KSM" get secrets no all-namespaces
check "$KSM" create deployments no all-namespaces

check "$PROMETHEUS" list pods no all-namespaces
check "$PROMETHEUS" get secrets no all-namespaces
check "$PROMETHEUS" create pods no all-namespaces

check_metrics() {
  verb=$1
  resource=$2
  expected=$3
  if actual=$(kubectl auth can-i "$verb" "$resource" \
    --all-namespaces --as="$METRICS_EXPORTER"); then
    command_status=0
  else
    command_status=$?
  fi

  case "$actual" in
    yes)
      [ "$command_status" -eq 0 ] || {
        printf 'FAIL: kubectl auth can-i %s %s exited with status %s\n' \
          "$verb" "$resource" "$command_status" >&2
        exit 1
      }
      ;;
    no)
      [ "$command_status" -eq 1 ] || {
        printf 'FAIL: kubectl auth can-i %s %s exited with status %s\n' \
          "$verb" "$resource" "$command_status" >&2
        exit 1
      }
      ;;
    *)
      printf 'FAIL: kubectl auth can-i %s %s returned unexpected output: %s\n' \
        "$verb" "$resource" "$actual" >&2
      exit 1
      ;;
  esac

  if [ "$actual" != "$expected" ]; then
    printf 'FAIL: metrics exporter %s %s: expected %s, got %s\n' \
      "$verb" "$resource" "$expected" "$actual" >&2
    exit 1
  fi
  printf 'PASS: metrics exporter %s %s = %s\n' "$verb" "$resource" "$actual"
}

check_metrics list pods.metrics.k8s.io yes
check_metrics list nodes.metrics.k8s.io yes
check_metrics watch pods.metrics.k8s.io no
check_metrics create pods.metrics.k8s.io no
check "$METRICS_EXPORTER" list pods no all-namespaces
check "$METRICS_EXPORTER" get secrets no all-namespaces
check "$METRICS_EXPORTER" create pods no all-namespaces
