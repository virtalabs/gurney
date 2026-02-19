#!/usr/bin/env bash
# Validate reproducible teardown: run scenario twice, diff output, check no orphaned resources.
# Use smoke-minimal when BlueFlow/TapirX not built; use smoke-docker for full stack.
set -euo pipefail

SCENARIO="${1:-smoke-minimal}"

echo "=== Run 1 ==="
uv run testbed run "$SCENARIO" 2>&1 | tee /tmp/run1.log
echo "=== Run 2 ==="
uv run testbed run "$SCENARIO" 2>&1 | tee /tmp/run2.log

# Normalize timestamps, diff
sed 's/[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}T[^ ]*/TS/g' /tmp/run1.log > /tmp/r1.norm
sed 's/[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}T[^ ]*/TS/g' /tmp/run2.log > /tmp/r2.norm
sed -i.bak 's/([0-9.]*s)/(DURATION)/g' /tmp/r1.norm /tmp/r2.norm 2>/dev/null || \
  sed -i '' 's/([0-9.]*s)/(DURATION)/g' /tmp/r1.norm /tmp/r2.norm
diff /tmp/r1.norm /tmp/r2.norm || { echo "FAIL: runs differ"; exit 1; }

# No orphans (exit 1 if any found)
ORPHAN_C=$(docker ps -a --filter label=testbed.managed=true -q 2>/dev/null | wc -l)
ORPHAN_N=$(docker network ls --filter label=testbed.managed=true -q 2>/dev/null | wc -l)
if [ "$ORPHAN_C" -gt 0 ] || [ "$ORPHAN_N" -gt 0 ]; then
  echo "FAIL: orphaned resources (containers: $ORPHAN_C, networks: $ORPHAN_N)"
  exit 1
fi

echo "REPRODUCIBLE TEARDOWN VALIDATED ($SCENARIO)"
