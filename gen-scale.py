#!/usr/bin/env python3
"""Generate the scale pod: bmp-scale.clab.yml + frr/scale/*.conf + scale.env.

The story part (r1..r6, netom, consumers, monitoring) comes from base.clab.yml.
On top, this script adds a hub-and-leaf "scale pod":

  hub     172.20.20.20    AS 65200, announces SHARED prefixes (20.0.0.0/8)
                          + CHURN prefixes (21.0.0.0/16) to every leaf
  leafNN  172.20.20.21+   AS 65200+NN, announces OWN prefixes (10.NN.0.0/16)
                          to the hub; link 172.31.NN.0/30 (hub .1, leaf .2)

The hub only sends its own prefixes to the leaves and the leaves only send
their own prefixes to the hub, so netom ends up with
  N x (SHARED + CHURN) x 2   (each leaf, pre + post policy)
+ N x OWN x 2                (hub, pre + post policy)
paths from the pod. Hub and leaves start WITHOUT a BMP target;
demo/3-scale-up.sh adds it live.

The hub's link addresses are in its FRR config (not in a containerlab exec),
so the hub's node definition does not depend on the number of leaves and
`./lab.sh scale` can add or remove leaves without recreating the hub.

Usage:  ./gen-scale.py [-n LEAVES] [-H SHARED] [-k OWN] [-c CHURN]
Values not given are taken from scale.env (the last run), else the defaults.
"""
import argparse, glob, ipaddress, os, re

here = os.path.dirname(os.path.abspath(__file__))
STORY_PATHS, STORY_PREFIXES = 47, 10   # r1..r6 in netom (measured)

cur = {"LEAVES": 30, "SHARED": 8000, "OWN": 500, "CHURN": 1000}
if os.path.exists(f"{here}/scale.env"):
    for line in open(f"{here}/scale.env"):
        k, _, v = line.strip().partition("=")
        if k in cur:
            cur[k] = int(v)

ap = argparse.ArgumentParser(description="Generate the scale pod (see module docstring).")
ap.add_argument("-n", "--leaves", type=int, default=cur["LEAVES"], help=f"number of leaves, 1-79 (now {cur['LEAVES']})")
ap.add_argument("-H", "--shared", type=int, default=cur["SHARED"], help=f"prefixes the hub sends every leaf (now {cur['SHARED']})")
ap.add_argument("-k", "--own", type=int, default=cur["OWN"], help=f"prefixes each leaf announces (now {cur['OWN']})")
ap.add_argument("-c", "--churn", type=int, default=cur["CHURN"], help=f"hub prefixes toggled by demo/4-churn.sh (now {cur['CHURN']})")
a = ap.parse_args()
if not 1 <= a.leaves <= 79:
    ap.error("--leaves: 1..79 (mgmt IPs .21-.99)")
if not (1 <= a.shared <= 65536 and 1 <= a.churn <= 4096 and 1 <= a.own <= 4096):
    ap.error("--shared 1..65536, --churn 1..4096, --own 1..4096")

os.makedirs(f"{here}/frr/scale", exist_ok=True)

BMP = """ bmp targets netom-collector
  bmp stats interval 15000
  bmp monitor ipv4 unicast pre-policy
  bmp monitor ipv4 unicast post-policy
  bmp connect 172.20.20.101 port 11019 min-retry 1000 max-retry 5000
 exit"""


def subnets(net, prefixlen, count):
    it = ipaddress.ip_network(net).subnets(new_prefix=prefixlen)
    return [str(next(it)) for _ in range(count)]


def prefixlen_for(base_len, count):
    n = base_len
    while 2 ** (n - base_len) < count:
        n += 1
    return max(n, 24)


def write(path, lines):
    # Rewrite in place (same inode), so bind mounts in running containers see it.
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


# ------------------------------------------------------------------ hub
# Prefixes are BGP "network" statements (no static routes): with FRR 10,
# 11k static routes go through mgmtd and were only partly loaded when 40+
# routers booted at once.
churn_len = prefixlen_for(16, a.churn)
lines = ["frr defaults traditional", "hostname hub", "log stdout warnings", "!"]
for i in range(1, a.leaves + 1):
    lines += [f"interface eth{i}", f" ip address 172.31.{i}.1/30", "exit"]
lines += ["!",
          "ip prefix-list HUB_OWN seq 5 permit 20.0.0.0/8 le 32",
          "ip prefix-list HUB_OWN seq 10 permit 21.0.0.0/16 le 32",
          "!",
          "! Only the hub's own prefixes go to the leaves (no leaf-to-leaf echo).",
          "! Never change this route-map live: FRR would re-send the whole table to every leaf.",
          "route-map TO_LEAF permit 10", " match ip address prefix-list HUB_OWN", "exit", "!",
          "bgp route-map delay-timer 1", "!",
          "router bgp 65200",
          " bgp router-id 10.255.0.254",
          " no bgp ebgp-requires-policy",
          " no bgp network import-check",
          " neighbor LEAVES peer-group",
          " neighbor LEAVES remote-as external",
          " ! Only the leaves open the TCP session. Without this, simultaneous opens",
          " ! (BGP connection collisions) at boot made FRR send BMP Peer Ups with an",
          " ! empty 19-byte 'received OPEN', which netom 0.6.0 rejects -> that leaf's",
          " ! routes were missing in netom.",
          " neighbor LEAVES passive"]
lines += [f" neighbor 172.31.{i}.2 peer-group LEAVES" for i in range(1, a.leaves + 1)]
lines += [" !", " address-family ipv4 unicast", "  ! shared table: every leaf receives these"]
lines += [f"  network {p}" for p in subnets("20.0.0.0/8", 24, a.shared)]
lines += ["  ! churn block: demo/4-churn.sh removes / re-adds these network statements"]
lines += [f"  network {p}" for p in subnets("21.0.0.0/16", churn_len, a.churn)]
lines += ["  neighbor LEAVES soft-reconfiguration inbound",
          "  neighbor LEAVES route-map TO_LEAF out",
          " exit-address-family", "exit", "!"]
write(f"{here}/frr/scale/hub.conf", lines)

# ------------------------------------------------------------------ leaves
own_len = prefixlen_for(16, a.own)
for i in range(1, a.leaves + 1):
    n = f"leaf{i:02d}"
    lines = ["frr defaults traditional", f"hostname {n}", "log stdout warnings", "!",
             f"ip prefix-list OWN seq 5 permit 10.{i}.0.0/16 le 32",
             "! demo/4-churn.sh adds / removes 'route-map TO_HUB deny 5' (withdraws everything)",
             "route-map TO_HUB permit 10", " match ip address prefix-list OWN", "exit", "!",
             "bgp route-map delay-timer 1", "!",
             f"router bgp {65200 + i}",
             f" bgp router-id 10.255.0.{i}",
             " no bgp ebgp-requires-policy",
             " no bgp network import-check",
             f" neighbor 172.31.{i}.1 remote-as 65200",
             f" neighbor 172.31.{i}.1 description hub",
             f" neighbor 172.31.{i}.1 timers connect 5",
             " !", " address-family ipv4 unicast"]
    lines += [f"  network {p}" for p in subnets(f"10.{i}.0.0/16", own_len, a.own)]
    lines += [f"  neighbor 172.31.{i}.1 soft-reconfiguration inbound",
              f"  neighbor 172.31.{i}.1 route-map TO_HUB out",
              " exit-address-family", "exit", "!"]
    write(f"{here}/frr/scale/{n}.conf", lines)

for f in glob.glob(f"{here}/frr/scale/leaf*.conf"):   # leaves from a bigger earlier run
    if int(re.search(r"leaf(\d+)\.conf$", f).group(1)) > a.leaves:
        os.remove(f)

# ------------------------------------------------------------------ topology
base = open(f"{here}/base.clab.yml").read()
head, links = base.split("\n  links:\n")


def node(name, ip, execs):
    out = [f"    {name}:", "      kind: linux", "      image: quay.io/frrouting/frr:10.0.1",
           f"      mgmt-ipv4: {ip}", "      binds:",
           "        - ./frr/scale/daemons:/etc/frr/daemons",
           "        - ./frr/vtysh.conf:/etc/frr/vtysh.conf",
           f"        - ./frr/scale/{name}.conf:/etc/frr/frr.conf"]
    if execs:
        out += ["      exec:"] + [f"        - {e}" for e in execs]
    return "\n".join(out)


extra = ["", "    # ==========================================",
         f"    # Scale pod (gen-scale.py: {a.leaves} leaves, SHARED={a.shared}, OWN={a.own}, CHURN={a.churn})",
         "    # ==========================================",
         node("hub", "172.20.20.20", [])]
for i in range(1, a.leaves + 1):
    extra += ["", node(f"leaf{i:02d}", f"172.20.20.{20 + i}", [f"ip addr add 172.31.{i}.2/30 dev eth1"])]
links += "\n    # Scale pod\n" + "\n".join(
    f'    - endpoints: ["hub:eth{i}", "leaf{i:02d}:eth1"]' for i in range(1, a.leaves + 1))

topo = head.rstrip("\n") + "\n" + "\n".join(extra) + "\n\n  links:\n" + links.rstrip("\n") + "\n"
topo = ("# GENERATED by gen-scale.py - edit base.clab.yml or gen-scale.py, then regenerate.\n"
        f"# {a.leaves} leaves, SHARED={a.shared}, OWN={a.own}, CHURN={a.churn}\n" + topo)
open(f"{here}/bmp-scale.clab.yml", "w").write(topo)

# BMP target block used by demo/3-scale-up.sh and lab.sh
open(f"{here}/frr/scale/bmp-target.txt", "w").write(BMP + "\n")

paths = a.leaves * (a.shared + a.churn) * 2 + a.leaves * a.own * 2 + STORY_PATHS
prefixes = a.shared + a.churn + a.leaves * a.own + STORY_PREFIXES
open(f"{here}/scale.env", "w").write(
    f"LEAVES={a.leaves}\nSHARED={a.shared}\nOWN={a.own}\nCHURN={a.churn}\n"
    f"EXPECTED_PATHS={paths}\nEXPECTED_PREFIXES={prefixes}\n")
print(f"{a.leaves} leaves, SHARED={a.shared}, OWN={a.own}, CHURN={a.churn}: "
      f"after act 3 netom should hold {paths:,} paths / {prefixes:,} unique prefixes")
