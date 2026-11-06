# Shared helpers for the demo scripts and lab.sh. Every script sources this file.
#
# Each command is shown in prompt style before it runs (r3-edge# ..., netom> ...),
# so the audience can see what is happening. Scripts wait for Enter between steps.
# Without pauses:  NOPAUSE=1 ./1-base.sh

LAB=clab-bmp-demo
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NETOM_API=http://172.20.20.101:8080/api/v1
NETOM_M=http://172.20.20.101:8080/metrics
VM_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$VM_IP" ] || VM_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if ($i=="src") print $(i+1)}')"
GRAFANA_URL="http://$VM_IP:3000/d/netom-scale"
CHURN_PID=/tmp/netom-churn.pid

B=$'\e[1m'; D=$'\e[2m'; C=$'\e[36m'; Y=$'\e[33m'; N=$'\e[0m'

say()   { printf '\n%s▶ %s%s\n' "$B$Y" "$*" "$N"; }        # step heading
note()  { printf '%s  %s%s\n' "$D" "$*" "$N"; }            # explanatory line (dimmed)
pause() { [ "${NOPAUSE:-0}" = 1 ] && return 0; read -r -p "${D}  [Enter]${N}" _; }

# Show command on a router:  rtr r3-edge "show bgp summary"
rtr() {
  local node=$1; shift
  printf '%s%s# %s%s\n' "$C" "$node" "$*" "$N"
  docker exec "$LAB-$node" vtysh -c "$*"
}

# netom-cli on the collector / on consumer 1:  netom "show bmp routers"
netom() {
  printf '%snetom> %s%s\n' "$C" "$*" "$N"
  # shellcheck disable=SC2086  # words are passed separately on purpose
  docker exec "$LAB-netom" netom-cli $*
}
consumer() {
  printf '%snetom-consumer> %s%s\n' "$C" "$*" "$N"
  # shellcheck disable=SC2086
  docker exec "$LAB-netom-consumer" netom-cli $*
}

# Show and run a shell command:  sh_run "docker restart ..."
sh_run() {
  printf '%slab$ %s%s\n' "$C" "$*" "$N"
  eval "$*"
}

# Ingress id of a BMP router by hostname (connected, newest):  router_id r3-edge
router_id() {
  curl -sg "$NETOM_API/ingresses?filter[type]=bmp" \
    | jq -r --arg n "$1" '.data[] | select(.name == $n and ((.state // "") | ascii_downcase) == "connected") | .id' \
    | sort -n | tail -1
}

# Ingress id of one view (inPre/inPost) of a neighbour under a router:
#   view_id <router-id> 10.0.13.1 inPre
view_id() {
  curl -sg "$NETOM_API/ingresses" \
    | jq -r --argjson p "$1" --arg a "$2" --arg t "$3" \
        '.data[] | select(.parent_ingress == $p and .remote_addr == $a and .peer_rib_type == $t) | .id' \
    | sort -n | tail -1
}

# Scale pod routers (hub + leaves) that are running in this lab
pod_nodes() { docker ps --format '{{.Names}}' | sed -n "s/^$LAB-\(hub\|leaf[0-9]*\)$/\1/p" | sort; }
leaves()    { pod_nodes | grep '^leaf'; }

# Does a pod router have the BMP target?   has_bmp hub
has_bmp() { docker exec "$LAB-$1" vtysh -c "show running-config" 2>/dev/null | grep -q '^ bmp targets'; }

# Add / remove the BMP target on pod routers:  set_bmp on hub leaf01 ...
# Retries: right after a router starts, vtysh can briefly answer "bgpd is not running".
set_bmp() {
  local mode=$1 n asn try; shift
  for n in "$@"; do
    asn=$(awk '/^router bgp/ {print $3}' "$LAB_DIR/frr/scale/$n.conf")
    local args=(-c "configure terminal" -c "router bgp $asn")
    if [ "$mode" = on ]; then
      while IFS= read -r l; do [ -n "$l" ] && args+=(-c "$(echo "$l" | sed 's/^ *//')"); done < "$LAB_DIR/frr/scale/bmp-target.txt"
    else
      args+=(-c "no bmp targets netom-collector")
    fi
    for try in 1 2 3 4 5 6 7 8 9 10; do
      docker exec "$LAB-$n" vtysh "${args[@]}" >/dev/null 2>&1
      if [ "$mode" = on ]; then has_bmp "$n" && break; else has_bmp "$n" || break; fi
      sleep 1
    done
  done
}

# One metric value (summed over series) from a netom /metrics URL:
#   mval http://172.20.20.101:8080/metrics netom_rib_unit_num_items_total
mval() {
  curl -s -m 2 "$1" | awk -v m="$2" '$1 ~ "^"m"({|$)" {s += $2; f = 1} END {if (f) printf "%d\n", s; else print ""}'
}

# Connected peer views (inPre/inPost ...) at the collector
collector_views() {
  curl -sg "$NETOM_API/ingresses" \
    | jq '[.data[] | select(.peer_rib_type != null and ((.state // "") | ascii_downcase) == "connected")] | length'
}

now_ms() { date +%s%3N; }
secs()   { awk -v ms="$1" 'BEGIN {printf "%.1f", ms / 1000}'; }
num()    { echo "$1" | sed ':a;s/\B[0-9]\{3\}\>/,&/;ta'; }

# Start a consumer container and time it until it holds the full table.
#   1. netom's log says how many routes it dumped to this client, and how fast
#   2. done when the consumer has stored at least that many paths
#   start_consumer netom-consumer 172.20.20.102 8081
start_consumer() {
  local name=$1 ip=$2 port=$3 m="http://$2:8080/metrics"
  note "netom holds $(collector_views) peer views and $(num "$(mval $NETOM_M netom_rib_unit_num_items_total)") paths."
  local since; since=$(date -u +%Y-%m-%dT%H:%M:%S)
  local t0; t0=$(now_ms)
  sh_run "docker start $LAB-$name >/dev/null"
  local dump="" sent="" items=0 up=0 last=0 t
  while :; do
    t=$(( $(now_ms) - t0 ))
    if [ -z "$dump" ]; then
      dump=$(docker logs --since "$since" "$LAB-netom" 2>&1 | grep "bmp-out dump for $ip:" | grep "dump complete" | tail -1)
      [ -n "$dump" ] && sent=$(echo "$dump" | sed -n 's/.* \([0-9]*\) total routes.*/\1/p')
    fi
    items=$(mval "$m" netom_rib_unit_num_items_total); up=$(mval "$m" netom_bmp_state_num_up_peers_total)
    if [ "$t" -ge $((last + 500)) ]; then
      printf '\r  %5ss  peer views %4s  paths stored %12s ' "$(secs $t)" "${up:-0}" "$(num "${items:-0}")"; last=$t
    fi
    [ -n "$sent" ] && [ "${items:-0}" -ge "$sent" ] && break
    [ "$t" -gt 300000 ] && { echo; note "timeout after 300 s"; return 1; }
    sleep 0.1
  done
  printf '\r  %5ss  peer views %4s  paths stored %12s \n' "$(secs $t)" "$up" "$(num "$items")"
  local mb secs_dump
  mb=$(echo "$dump" | sed -n 's/.* \([0-9.]*\) MB in \([0-9.]*\)s.*/\1/p'); secs_dump=$(echo "$dump" | sed -n 's/.* MB in \([0-9.]*\)s.*/\1/p')
  say "netom sent $name $(num "$sent") routes ($mb MB) in ${secs_dump} s"
  say "$name had all of them stored $(secs $t) s after 'docker start' (container start + parsing)"
  note "UI: http://$VM_IP:$port/"
}
