#!/usr/bin/env bash
# Before the talk: back to the starting point of act 1.
#   - churn stopped, scale pod (hub + leaves) without BMP target
#   - both consumers stopped
#   - netom restarted with an empty RIB, only r1..r6 connected
source "$(dirname "$0")/_lib.sh"
export NOPAUSE=1

say "Stop churn"
"$(dirname "$0")/4-churn.sh" stop >/dev/null 2>&1 || true

say "Remove the BMP target from the scale pod (hub + $(leaves | wc -l) leaves)"
for n in $(pod_nodes); do set_bmp off "$n" & done
wait

say "Stop both consumers"
docker stop -t 30 "$LAB-netom-consumer" "$LAB-netom-consumer2" >/dev/null 2>&1

say "Restart netom (empty RIB) and wait for the 6 story routers"
docker restart "$LAB-netom" >/dev/null
for i in $(seq 1 60); do
  n=$(mval $NETOM_M netom_bmp_num_connected_routers_total)
  [ "${n:-0}" -ge 6 ] && break; sleep 1
done
sleep 5
note "BMP routers connected: $n   paths: $(mval $NETOM_M netom_rib_unit_num_items_total)"
note "Grafana: $GRAFANA_URL   (set the time range to 'Last 10 minutes')"
[ "${n:-0}" = 6 ] && say "Ready for act 1." || say "WARNING: expected 6 routers, got $n"
