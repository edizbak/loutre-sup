# Loutre supervision

This repository contains a **development-only** Kubernetes monitoring profile. It is not
production-ready. Workload deployment is still pending; check [the deployment status and
decisions](./STACK-PLAN.md) before continuing.

## What is included

`k8s/dev/` contains Prometheus, kube-state-metrics, Grafana and a cluster overview dashboard,
scoped to `monitoring-dev`. Services are internal ClusterIP; Grafana anonymous access and
self-signup are disabled. Prometheus and Grafana use bounded ephemeral storage. Alerts are
evaluated, but no notification receiver is configured.

The development profile has no Ingress, persistent volumes, node-exporter, external probes,
or NetworkPolicies; reachability from other cluster pods is accepted for dev only. ClusterIP
is not an authorization boundary between pods. Production must add and test network isolation
against unrelated or compromised workloads. Review the inventory and open decisions in
[STACK-PLAN.md](./STACK-PLAN.md) before any installation.

## Validate locally

Podman is the only host-side prerequisite. From this repository, run:

```sh
sh scripts/validate.sh
```

This runs contract tests, Prometheus configuration and rule tests, renders the three
Kustomize targets (namespace, bootstrap, workloads), validates their Kubernetes schemas, and
smoke-tests the pinned kube-state-metrics image. It does not need cluster credentials and
does not apply manifests.

## Guides

- [Development deployment runbook](./docs/development-deployment.md) — staged namespace,
  RBAC, restricted kubeconfig, deployment, verification, and rollback.
- [Grafana admin credential setup](./docs/grafana-admin-secret.md) — interactive creation
  and safe verification of the Kubernetes Secret Grafana requires.
- [CI, versioning and GitOps](./docs/ci-versioning-gitops.md) — validation workflow,
  commit/release conventions, and staged Argo CD proposal.
- [Argo CD dev observation](./docs/argocd-dev-observe.md) — repository-side AppProject and
  Application, prerequisites, validation, and gates before cluster changes.
- [Stack plan and inventory](./STACK-PLAN.md) — architecture, known cluster constraints,
  milestones, and decisions still needed.

Never commit or share credentials, kubeconfigs, tokens, or Secret data.
