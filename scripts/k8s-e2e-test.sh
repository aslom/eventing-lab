#!/usr/bin/env sh
# Shim only — the implementation is k8s_e2e_test.py (docs/specs/0002-phase1-keda-kubernetes.md §9, §14).
exec python3 "$(dirname "$0")/k8s_e2e_test.py" "$@"
