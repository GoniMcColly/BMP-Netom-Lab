#!/usr/bin/env bash
# Act 4: generate real BGP churn (announcements + withdrawals) in the background until stopped.
#   ./4-churn.sh [start]   start (default)
#   ./4-churn.sh stop      stop and restore all routes
#
# Every PERIOD seconds (default 8):
#   - the hub withdraws / re-announces its churn block (21.0.0.0/16) to ALL leaves,
#     so every leaf reports the change through BMP (pre + post)
#   - one random leaf withdraws its own prefixes for a moment and re-announces them
source "$(dirname "$0")/_lib.sh"
PERIOD=${PERIOD:-8}

# Hub churn = remove / re-add the 'network' statements of the churn block.
# (Toggling an OUTBOUND route-map instead would make FRR re-send the whole
# table to every leaf: a policy change moves the peers to a new update-group.)
hub_churn_files() {
  local conf="$LAB_DIR/frr/scale/hub.conf"
  { echo "router bgp 65200"; echo " address-family ipv4 unicast"; grep '^  network 21\.' "$conf" | sed 's/^  network/  no network/'; } > /tmp/churn-off.conf
  { echo "router bgp 65200"; echo " address-family ipv4 unicast"; grep '^  network 21\.' "$conf"; } > /tmp/churn-on.conf
  docker cp /tmp/churn-off.conf "$LAB-hub:/tmp/" && docker cp /tmp/churn-on.conf "$LAB-hub:/tmp/"
}
hub_churn() { # on|off: off = the hub withdraws the churn block from all leaves
  docker exec "$LAB-hub" vtysh -f "/tmp/churn-$1.conf" >/dev/null 2>&1
}
leaf_redist() { # leaf on|off: off = the leaf withdraws all its own prefixes
  if [ "$2" = off ]; then
    docker exec "$LAB-$1" vtysh -c "configure terminal" -c "route-map TO_HUB deny 5" >/dev/null
  else
    docker exec "$LAB-$1" vtysh -c "configure terminal" -c "no route-map TO_HUB deny 5" >/dev/null 2>&1
  fi
}

case "${1:-start}" in
stop)
  if [ -f "$CHURN_PID" ]; then kill "$(cat "$CHURN_PID")" 2>/dev/null; rm -f "$CHURN_PID"; fi
  sleep 1
  hub_churn_files; hub_churn on
  for l in $(leaves); do leaf_redist "$l" on & done; wait
  say "Churn stopped, all routes announced again."
  ;;
start)
  [ -f "$CHURN_PID" ] && kill -0 "$(cat "$CHURN_PID")" 2>/dev/null && { note "already running (pid $(cat "$CHURN_PID"))"; exit 0; }
  C=$(docker exec "$LAB-hub" vtysh -c "show running-config" | grep -c '^  network 21\.')
  NL=$(leaves | wc -l)
  hub_churn_files
  say "Start BGP churn: every ${PERIOD} s"
  note "hub: withdraw / re-announce $C prefixes to all $NL leaves  -> $NL routers report it via BMP"
  note "plus one random leaf drops its own prefixes and brings them back"
  note "Stop with: $0 stop"
  (
    trap 'exit 0' TERM
    state=on
    while :; do
      [ $state = on ] && state=off || state=on
      hub_churn $state
      l=$(leaves | shuf -n 1); leaf_redist "$l" off; sleep 2; leaf_redist "$l" on
      sleep $((PERIOD - 2))
    done
  ) >/dev/null 2>&1 &
  echo $! > "$CHURN_PID"
  note "running in the background (pid $!)"
  ;;
esac
