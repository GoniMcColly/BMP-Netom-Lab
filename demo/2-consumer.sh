#!/usr/bin/env bash
# Act 2: the first downstream consumer. netom re-streams BMP: full state first, then live updates.
source "$(dirname "$0")/_lib.sh"

say "Start netom-consumer. Connected to netom's restream port :11020."
start_consumer netom-consumer 172.20.20.102 8081
pause

say "To the consumer, netom looks like one big BMP router"
consumer "show bmp routers"
pause

consumer "show ip bgp 203.0.113.0/24"
