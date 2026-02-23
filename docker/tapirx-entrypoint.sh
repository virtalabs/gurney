#!/bin/sh
# Entrypoint for TapirX container.
# Runs TapirX with the given arguments. Use sh for Alpine (no bash).

set -eu
ip link set eth0 promisc on >/dev/null 2>&1 || true

exec tapirx "$@"