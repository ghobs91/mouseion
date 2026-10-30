#!/usr/bin/env bash
# Offline + one keyless live check. Safe to run before a demo.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== offline tests"
.venv/bin/pytest -q

echo "== source-policy audit"
.venv/bin/mouseion audit

echo "== jurisdiction registry"
.venv/bin/mouseion civic list > /dev/null

echo "== local readiness"
.venv/bin/mouseion doctor

echo "== keyless lookup (npm package)"
.venv/bin/mouseion lookup "react" --provider npm --capability package --limit 1

echo "smoke: ok"
