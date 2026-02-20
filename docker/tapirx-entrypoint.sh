#!/bin/sh
# Entrypoint for TapirX container.
# Runs TapirX with the given arguments. Use sh for Alpine (no bash).

set -eu

#if printf -- '%s\n' "$@" | grep -qE '^.*-iface.*$'; then
#    INTERFACE=$(printf -- '%s\n' "$@" | grep -oE '^-iface [^ ]+' | cut -d' ' -f2)
#    ip link set "$INTERFACE" promisc on >/dev/null 2>&1 || true
#fi
ip link set eth0 promisc on >/dev/null 2>&1 || true

exec tapirx "$@"