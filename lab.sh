#!/usr/bin/env bash
# Lab control. Run on the lab host as root, or as a user in the docker and clab_admins groups.
#
#   ./lab.sh deploy  [-n LEAVES] [-H SHARED] [-k OWN] [-c CHURN]   fresh deploy, then ready for act 1
#   ./lab.sh scale   [-n LEAVES] [-H SHARED] [-k OWN] [-c CHURN]   resize the RUNNING lab live
#   ./lab.sh status                                                 size and counts
#   ./lab.sh destroy                                                remove the lab
#
# Options not given keep their current value (scale.env). Defaults: 30 / 8000 / 500 / 1000.
#   LEAVES  leaf routers (1-79)
#   SHARED  prefixes the hub announces to every leaf
#   OWN     prefixes every leaf announces to the hub
#   CHURN   hub prefixes that demo/4-churn.sh withdraws and re-announces
cd "$(dirname "$0")" || exit 1
source demo/_lib.sh
TOPO=bmp-scale.clab.yml

need() { for c in "$@"; do command -v "$c" >/dev/null || { echo "missing: $c"; exit 1; }; done; }
running() { docker ps --format '{{.Names}}' | grep -qx "$LAB-hub"; }
cons_running() { docker ps --format '{{.Names}}' | grep -x "$LAB-netom-consumer2\?"; }

# Wait until every leaf has its BGP session to the hub and all prefixes moved both ways.
wait_pod() {
  source scale.env
  local t0 s est rcd ok l
  t0=$(date +%s)
  while :; do
    s=$(docker exec "$LAB-hub" vtysh -c 'show bgp summary json' 2>/dev/null \
        | jq -r '[.ipv4Unicast.peers[]? | select(.state == "Established")] | "\(length) \(map(.pfxRcd) | add // 0)"')
    est=${s% *}; rcd=${s#* }
    ok=0
    if [ "${est:-0}" = "$LEAVES" ] && [ "${rcd:-0}" = $((LEAVES * OWN)) ]; then
      ok=$(for l in $(leaves); do
             docker exec "$LAB-$l" vtysh -c 'show bgp summary json' 2>/dev/null \
               | jq -r '[.ipv4Unicast.peers[]?.pfxRcd] | add // 0' &
           done | grep -cx $((SHARED + CHURN)))
    fi
    printf '\r  %4ss  hub: %3s/%s leaves up, %7s/%s prefixes received; leaves complete: %3s/%s ' \
      $(( $(date +%s) - t0 )) "${est:-0}" "$LEAVES" "${rcd:-0}" $((LEAVES * OWN)) "$ok" "$LEAVES"
    [ "$ok" = "$LEAVES" ] && { echo; return 0; }
    [ $(( $(date +%s) - t0 )) -gt 600 ] && { echo; echo "  timeout: the pod did not converge"; return 1; }
    sleep 2
  done
}

cmd=${1:-help}; shift
case "$cmd" in
deploy)
  need containerlab docker jq curl python3
  ./gen-scale.py "$@" || exit 1
  say "Deploy (containerlab --reconfigure: all nodes start fresh)"
  containerlab deploy -t "$TOPO" --reconfigure || exit 1
  say "Wait for the scale pod to converge"
  wait_pod || exit 1
  demo/0-prepare.sh
  ;;

scale)
  need containerlab docker jq python3
  running || { echo "Lab is not running. Use: ./lab.sh deploy $*"; exit 1; }
  source scale.env; OLD=$LEAVES
  old=$(mktemp -d); cp frr/scale/*.conf "$old/"
  bmp=off; has_bmp hub && bmp=on
  cons=$(cons_running)
  if [ -f "$CHURN_PID" ]; then say "Stop churn first"; demo/4-churn.sh stop; fi

  ./gen-scale.py "$@" || exit 1
  source scale.env

  say "Apply config changes live (vtysh, no restarts)"
  apply() { # node: diff the old and new config file and apply only the difference
    local d; d=$(tools/frr-diff.py "$old/$1.conf" "frr/scale/$1.conf")
    [ -z "$d" ] && return 0
    echo "$d" | docker exec -i "$LAB-$1" sh -c 'cat > /tmp/scale.conf && vtysh -f /tmp/scale.conf >/dev/null 2>&1'
    printf '  %-7s %5s config lines\n' "$1" "$(echo "$d" | wc -l)"
  }
  apply hub
  for i in $(seq 1 $(( OLD < LEAVES ? OLD : LEAVES ))); do apply "$(printf 'leaf%02d' "$i")" & done
  wait

  if [ "$LEAVES" != "$OLD" ]; then
    if [ "$bmp" = on ] && [ "$LEAVES" -lt "$OLD" ]; then
      # Close their BMP sessions first. A removed container never sends a TCP FIN,
      # and BMP has no keepalive: netom would show the router as connected forever.
      say "Remove the BMP target from the leaves that go away"
      for i in $(seq $((LEAVES + 1)) "$OLD"); do set_bmp off "$(printf 'leaf%02d' "$i")" & done
      wait; sleep 2
    fi
    say "Leaves $OLD -> $LEAVES (containerlab adds / removes only those nodes)"
    containerlab deploy -t "$TOPO" 2>&1 | grep -E 'Creating (node|container)|Deleting node|ERRO|rror' | sed 's/^/  /'
    # containerlab starts stopped nodes again: keep the consumers as they were
    for c in netom-consumer netom-consumer2; do
      echo "$cons" | grep -qx "$LAB-$c" || docker stop -t 30 "$LAB-$c" >/dev/null 2>&1 &
    done
    wait
  fi
  rm -rf "$old"

  if [ "$bmp" = on ] && [ "$LEAVES" -gt "$OLD" ]; then
    say "BMP was on in the pod: add the BMP target to the new leaves"
    for i in $(seq $((OLD + 1)) "$LEAVES"); do
      n=$(printf 'leaf%02d' "$i")
      until docker exec "$LAB-$n" vtysh -c 'show bgp summary json' 2>/dev/null | jq -e '.ipv4Unicast' >/dev/null 2>&1; do sleep 1; done
      set_bmp on "$n" &
    done
    wait
  fi
  say "Wait for the scale pod to converge"
  wait_pod
  if [ "$bmp" = on ]; then  # and for netom to finish ingesting
    p=-1; while q=$(mval $NETOM_M netom_rib_unit_num_items_total); [ "$q" != "$p" ]; do p=$q; sleep 3; done
  fi
  "$0" status
  [ "$bmp" = on ] && note "netom counts withdrawn records too: after shrinking, run demo/0-prepare.sh + demo/3-scale-up.sh for exact numbers"
  ;;

status)
  source scale.env
  say "Size: $LEAVES leaves (running: $(leaves | wc -l)), SHARED=$SHARED, OWN=$OWN, CHURN=$CHURN"
  note "expected in netom after act 3: $(num "$EXPECTED_PATHS") paths, $(num "$EXPECTED_PREFIXES") unique prefixes"
  r=$(curl -sg -m 2 "$NETOM_API/ingresses?filter[type]=bmp" | jq '[.data[] | select((.state // "") | ascii_downcase == "connected")] | length')
  note "netom now: ${r:-?} BMP routers connected, $(num "$(mval $NETOM_M netom_rib_unit_num_items_total)") paths, $(num "$(mval $NETOM_M netom_rib_unit_num_unique_prefixes_total)") unique prefixes"
  note "pod BMP target: $(has_bmp hub && echo on || echo off); churn: $([ -f "$CHURN_PID" ] && echo running || echo off); consumers running: $(cons_running | sed "s/^$LAB-//" | xargs)"
  note "Grafana: $GRAFANA_URL   netom UI: http://$VM_IP:8080/"
  ;;

destroy)
  [ -f "$CHURN_PID" ] && { kill "$(cat "$CHURN_PID")" 2>/dev/null; rm -f "$CHURN_PID"; }
  containerlab destroy -t "$TOPO" --cleanup
  ;;

*)
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
  ;;
esac
