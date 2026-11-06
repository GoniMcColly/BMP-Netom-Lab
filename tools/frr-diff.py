#!/usr/bin/env python3
"""Print the vtysh commands that turn FRR config OLD into NEW (for `vtysh -f`).

Used by `lab.sh scale` to change running hub / leaf routers live, without a
restart. It only understands the structure gen-scale.py writes (top-level
lines, one level of blocks like `interface` / `router bgp`, and
`address-family` inside `router bgp`), which is all the scale pod needs.
Lines that only exist at runtime (e.g. the BMP target added in act 3) are
not in either file and therefore never touched.

Usage: frr-diff.py OLD.conf NEW.conf
"""
import sys


def parse(path):
    """Return {context tuple: [lines]} with comments and block ends removed."""
    ctx, out = (), {}
    for raw in open(path):
        line = raw.rstrip("\n")
        s = line.strip()
        if not s or s.startswith("!"):
            continue
        if s in ("exit", "end"):
            if not line.startswith(" "):
                ctx = ()
            continue
        if s == "exit-address-family":
            ctx = ctx[:1]
            continue
        depth = len(line) - len(line.lstrip(" "))
        if depth == 0:
            ctx = ()
        if s.startswith("address-family ") and ctx:
            ctx = ctx[:1] + (s,)
            out.setdefault(ctx, [])
            continue
        if depth == 0 and s.split()[0] in ("interface", "router", "route-map"):
            ctx = (s,)
            out.setdefault(ctx, [])
            continue
        out.setdefault(ctx, []).append(s)
    return out


def negate(s):
    w = s.split()
    if w[0] == "neighbor" and len(w) >= 4 and w[2] == "peer-group":
        return f"no neighbor {w[1]}"          # remove the neighbor itself
    return "no " + s


old, new = parse(sys.argv[1]), parse(sys.argv[2])
cmds = []
# deeper contexts (address-family) after their parent, so new neighbors exist first
for ctx in sorted(set(old) | set(new), key=lambda c: (len(c), c)):
    o, n = old.get(ctx, []), new.get(ctx, [])
    so, sn = set(o), set(n)
    rm = [negate(l) for l in o if l not in sn]
    add = [l for l in n if l not in so]
    if not rm and not add:
        continue
    cmds += list(ctx) + rm + add
    if len(ctx) == 2:
        cmds.append("exit-address-family")
    if ctx:
        cmds.append("exit")
if cmds:
    print("\n".join(cmds))
