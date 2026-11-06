#!/usr/bin/env bash
# Act 5: a second consumer joins while the network is churning.
source "$(dirname "$0")/_lib.sh"

say "Start a second consumer, while BGP is churning"
note "The routers are not involved: netom sends it its own copy of the state, then the live updates."
start_consumer netom-consumer2 172.20.20.103 8082
pause

say "Both consumers stay in sync with live updates"
for c in "netom:172.20.20.101" "netom-consumer:172.20.20.102" "netom-consumer2:172.20.20.103"; do
  printf '  %-16s paths %12s   last update %ss ago\n' "${c%%:*}" "$(num "$(mval "http://${c#*:}:8080/metrics" netom_rib_unit_num_items_total)")" \
    "$(curl -s "http://${c#*:}:8080/metrics" | awk '/^netom_since_last_update_seconds\{component="rib"/ {print $2}')"
done
