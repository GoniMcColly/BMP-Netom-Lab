#!/usr/bin/env bash
# Act 1: a few routers, one netom. Pre-policy vs. post-policy, and the routing tables.
source "$(dirname "$0")/_lib.sh"

say "Six routers stream BMP to netom. netom never logs into them."
netom "show bmp routers"
pause

R3=$(router_id r3-edge)
PRE=$(view_id "$R3" 10.0.13.1 inPre)
POST=$(view_id "$R3" 10.0.13.1 inPost)
say "r3-edge receives routes from r1-isp1. Its inbound route-map drops 10.99.99.0/24."
rtr r3-edge "show running-config" | awk '/^ip prefix-list BOGON/ {print} /^route-map FROM_ISP1/ {f=1} f {print} f && /^exit/ {f=0}'
pause

say "netom's diff page compares the two views of that session:"
note "left = received but not accepted, middle = accepted, right = only after policy (always empty: an inbound policy can't add prefixes)"
note "http://$VM_IP:8080/routes/diff/$PRE/$POST"
note "One peer view as a routing table: http://$VM_IP:8080/routes/$POST"
pause

say "And the network-wide table, assembled from all routers' BMP feeds"
netom "show ip bgp 203.0.113.0/24"
