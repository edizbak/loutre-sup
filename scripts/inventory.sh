#!/bin/sh
set -u

run() {
  printf '\n$'
  printf ' %s' "$@"
  printf '\n'
  "$@"
  status=$?
  if [ "$status" -ne 0 ]; then
    printf 'Command failed with exit status %s; continuing with remaining read-only checks.\n' "$status" >&2
  fi
}

printf '%s\n' '=== Kubernetes context and version ==='
run kubectl config current-context
run kubectl version -o yaml

printf '%s\n' '=== Nodes and resource metrics ==='
run kubectl get nodes -o wide
run kubectl top nodes
run kubectl top pods -A
run kubectl get apiservice v1beta1.metrics.k8s.io

printf '%s\n' '=== Storage ==='
run kubectl get storageclass -o wide
run kubectl get pvc -A -o wide
run kubectl get pv -o 'custom-columns=NAME:.metadata.name,CLASS:.spec.storageClassName,ACCESS:.spec.accessModes,CAPACITY:.spec.capacity.storage,CLAIM:.spec.claimRef.name,STATUS:.status.phase'

printf '%s\n' '=== Resource and network policies ==='
run kubectl get resourcequota,limitrange -A
run kubectl get networkpolicy -A

printf '%s\n' '=== Ingress and monitoring components ==='
run kubectl get ingressclass -o wide
run kubectl get ingress -A -o wide
run kubectl get pods,svc,deploy,ds,sts -A -o wide

printf '%s\n' '=== Recent cluster events ==='
run kubectl get events -A --sort-by=.lastTimestamp

printf '%s\n' '=== Host resources (run on the k3s server) ==='
run uname -a
run nproc
run free -h
run df -hT
run lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS
run systemctl is-active k3s
