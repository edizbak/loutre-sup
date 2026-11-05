# Project Guidelines

## Changes and commits

- Choose and create a small, coherent Conventional Commit after a meaningful change has passed its relevant local validation; do not leave routine commit creation to the user.
- Include only files relevant to that change. Preserve unrelated worktree changes, and never push unless the user explicitly asks.
- A validated commit is not approval to deploy it. Follow the documented GitOps and deployment gates; do not apply changes to a cluster without explicit approval for that operation.

## Validation

- Run `sh scripts/validate.sh` for changes to manifests, dashboards, tests, or validation tooling.
- Keep cluster credentials out of local validation and CI.
