#!/usr/bin/env bash
# Act 3: the hub and all leaves get a BMP target at the same moment.
source "$(dirname "$0")/_lib.sh"

NODES=$(pod_nodes); CNT=$(echo "$NODES" | wc -l)
say "Turn on BMP on $CNT more routers at once (hub + $((CNT - 1)) leaves)"
note "Each router gets this block (exactly what an operator adds to a real router):"
sed 's/^/    /' "$LAB_DIR/frr/scale/bmp-target.txt"
note "Watch Grafana: $GRAFANA_URL"
pause

R0=$(mval $NETOM_M netom_bmp_num_connected_routers_total); P0=$(mval $NETOM_M netom_rib_unit_num_items_total)
WANT=$((R0 + CNT))
t0=$(now_ms)
BATCH=${BATCH:-10}   # routers switched on together; batches 0.5 s apart keep the VM responsive
k=0
for n in $NODES; do
  set_bmp on "$n" &
  k=$((k + 1)); [ $((k % BATCH)) = 0 ] && sleep 0.5
done
wait
note "configured $CNT routers in $(secs $(( $(now_ms) - t0 ))) s"

# Wait until all routers are connected and the path count has been stable for 3 s
stable=0; prev=-1; last=0
while :; do
  r=$(mval $NETOM_M netom_bmp_num_connected_routers_total); p=$(mval $NETOM_M netom_rib_unit_num_items_total)
  t=$(( $(now_ms) - t0 ))
  if [ "$r" -ge "$WANT" ] && [ "$p" = "$prev" ] && [ "$p" -gt "$P0" ]; then stable=$((stable + 1)); else stable=0; [ "$p" != "$prev" ] && tdone=$t; fi
  prev=$p
  if [ "$t" -ge $((last + 500)) ]; then printf '\r  %5ss  routers %3s/%s  paths %12s ' "$(secs $t)" "$r" "$WANT" "$(num "$p")"; last=$t; fi
  [ "$stable" -ge 15 ] && break
  [ "$t" -gt 600000 ] && { echo; note "timeout"; break; }
  sleep 0.2
done
echo
say "netom: $r routers, $(num "$p") paths ($(num "$(mval $NETOM_M netom_rib_unit_num_unique_prefixes_total)") unique prefixes). Ingest done after $(secs $tdone) s."
source "$LAB_DIR/scale.env"
U=$(mval $NETOM_M netom_rib_unit_num_unique_prefixes_total)
note "expected from the configs: $(num $EXPECTED_PATHS) paths, $(num $EXPECTED_PREFIXES) unique prefixes"
[ "$U" = "$EXPECTED_PREFIXES" ] || note "WARNING: netom has $U unique prefixes (check 'docker logs $LAB-netom | grep Error')"
