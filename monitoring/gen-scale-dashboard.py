#!/usr/bin/env python3
# Usage: python3 monitoring/gen-scale-dashboard.py monitoring/grafana/dashboards/netom-scale.json
"""Generate monitoring/grafana/dashboards/netom-scale.json."""
import json, sys

PROM = {"type": "prometheus", "uid": "prom-netom"}
API = {"type": "yesoreyeram-infinity-datasource", "uid": "netom-api"}
MIXED = {"type": "datasource", "uid": "-- Mixed --"}

COLL = 'component="rib"'
CONS = 'component="consumer-rib"'
NETOM_C = 'name=~"clab-.+-netom"'
CONS_C = 'name=~"clab-.+-netom-consumer2?"'
ROUTER_C = 'name=~"clab-.+", name!~"clab-.+-(netom|netom-consumer2?|prometheus|grafana|cadvisor)"'

panels = []
_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def t(expr, legend="", ref="A", instant=False, fmt=None):
    d = {"datasource": PROM, "refId": ref, "expr": expr, "legendFormat": legend}
    if instant:
        d["instant"] = True
        d["range"] = False
    if fmt:
        d["format"] = fmt
    return d


def row(title, y):
    panels.append({"type": "row", "id": nid(), "title": title, "collapsed": False,
                   "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})


def stat(title, targets, x, y, w=4, h=4, unit="short", desc="", thresholds=None,
         color_mode="value", decimals=None, graph=True):
    fc = {"unit": unit, "color": {"mode": "thresholds"},
          "thresholds": {"mode": "absolute", "steps": thresholds or [{"color": "blue", "value": None}]}}
    if decimals is not None:
        fc["decimals"] = decimals
    panels.append({
        "type": "stat", "id": nid(), "title": title, "description": desc, "datasource": PROM,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets,
        "fieldConfig": {"defaults": fc, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": color_mode, "graphMode": "area" if graph else "none",
                    "textMode": "auto", "justifyMode": "center", "wideLayout": True}})


def ts(title, targets, x, y, w=12, h=8, unit="short", desc="", stack=False, overrides=None,
       fill=10, line=2, interp="stepAfter"):
    panels.append({
        "type": "timeseries", "id": nid(), "title": title, "description": desc, "datasource": PROM,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets,
        "fieldConfig": {"defaults": {
            "unit": unit, "min": 0, "color": {"mode": "palette-classic"},
            "custom": {"drawStyle": "line", "lineInterpolation": interp, "lineWidth": line,
                       "fillOpacity": fill, "showPoints": "never", "spanNulls": True,
                       "stacking": {"mode": "normal" if stack else "none", "group": "A"}}},
            "overrides": overrides or []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}}})


def color(name, c):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}


# ---------------------------------------------------------------- Row 1
y = 0
row("The whole network in one collector", y); y += 1
stat("BMP routers streaming", [t('netom_bmp_num_connected_routers_total{component="bmp-in"}')], 0, y,
     desc="Routers with an open BMP session to netom (one TCP session each).",
     thresholds=[{"color": "green", "value": None}])
stat("BGP sessions monitored", [t('sum(netom_bmp_state_num_up_peers_total{component="bmp-in"})')], 4, y,
     desc="BGP sessions in state Up, summed over all routers. Each one is a peer whose Adj-RIB-In netom receives.")
stat("Paths in netom", [t(f'netom_rib_unit_num_items_total{{{COLL}}}')], 8, y,
     desc="RIB records in the collector: one per prefix per peer view (pre- and post-policy count separately). Includes records that were withdrawn.",
     thresholds=[{"color": "purple", "value": None}])
stat("Unique prefixes", [t(f'netom_rib_unit_num_unique_prefixes_total{{{COLL}}}')], 12, y,
     desc="Distinct prefixes across all routers and peers.")
stat("Ingest rate", [t('sum(rate(netom_bmp_state_num_announcements_total{component="bmp-in"}[10s])) or vector(0)')],
     16, y, unit="short", decimals=0,
     desc="Prefix announcements per second parsed from all BMP sessions (10 s window).",
     thresholds=[{"color": "orange", "value": None}])
stat("netom memory", [t(f'sum(container_memory_working_set_bytes{{{NETOM_C}}})')], 20, y, unit="bytes",
     desc="Working set of the netom container (cAdvisor).",
     thresholds=[{"color": "text", "value": None}])
y += 4

# ---------------------------------------------------------------- Row 2
row("Late joiner: a new consumer gets the full table from netom, not from the routers", y); y += 1
CONS2 = 'component="consumer2-rib"'
ts("Paths held: collector vs. consumers", [
    t(f'netom_rib_unit_num_items_total{{{COLL}}}', "collector (netom)", "A"),
    t(f'netom_rib_unit_num_items_total{{{CONS}}}', "consumer 1", "B"),
    t(f'netom_rib_unit_num_items_total{{{CONS2}}}', "consumer 2", "C"),
], 0, y, w=16, h=10, interp="linear", fill=0, line=3,
   desc="When a consumer starts, its line climbs to the collector's line. Every path it gets comes from netom's RIB; the routers send nothing extra.",
   overrides=[color("collector (netom)", "purple"), color("consumer 1", "green"), color("consumer 2", "yellow")])
for i, (lbl, comp) in enumerate([("Consumer 1", CONS), ("Consumer 2", CONS2)]):
    stat(f"{lbl}: paths vs. netom", [t(f'100 * netom_rib_unit_num_items_total{{{comp}}} / on() netom_rib_unit_num_items_total{{{COLL}}}')],
         16, y + 5 * i, w=4, h=5, unit="percent", decimals=1, color_mode="background", graph=False,
         desc="Paths the consumer has stored, as a share of netom's. netom's count includes withdrawn records, so a consumer that joins during churn shows slightly less until those prefixes are announced again.",
         thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 50}, {"color": "green", "value": 99}])
    panels[-1]["fieldConfig"]["defaults"]["noValue"] = "not running"
stat("Consumers connected", [t('netom_bmp_tcp_out_clients_connected_total{component="bmp-out"} - netom_bmp_tcp_out_clients_disconnected_total{component="bmp-out"}')],
     20, y, w=4, h=5, graph=False,
     desc="Open sessions on netom's restream port (bmp-tcp-out :11020).",
     thresholds=[{"color": "green", "value": None}])
stat("Router BMP messages, last 30 s", [t('sum(increase(netom_bmp_tcp_in_num_bmp_messages_received_total{component="bmp-in"}[30s])) or vector(0)')],
     20, y + 5, w=4, h=5, decimals=0, graph=False,
     desc="Messages the routers sent netom in the last 30 s. A consumer joining does not change this: the routers are not involved.",
     thresholds=[{"color": "text", "value": None}])
y += 10

# ---------------------------------------------------------------- Row 3
row("Ingest: what the routers are telling netom", y); y += 1
ts("Announcements / withdrawals per second (all routers)", [
    t('sum(rate(netom_bmp_state_num_announcements_total{component="bmp-in"}[10s]))', "announcements", "A"),
    t('-sum(rate(netom_bmp_state_num_withdrawals_total{component="bmp-in"}[10s]))', "withdrawals", "B"),
], 0, y, w=12, h=8, interp="linear",
   overrides=[color("announcements", "green"), color("withdrawals", "red")],
   desc="Withdrawals are drawn below zero.")
panels[-1]["fieldConfig"]["defaults"].pop("min")
ts("BMP messages per second by type", [
    t('sum by (msg_type) (rate(netom_bmp_tcp_in_num_bmp_messages_received_total{component="bmp-in"}[10s]))', "{{msg_type}}"),
], 12, y, w=12, h=8, stack=True, interp="linear")
y += 8

# Per-router table: Prometheus values joined with sysName from the netom API.
panels.append({
    "type": "table", "id": nid(), "title": "Per router: prefixes and sessions (sysName from netom's API)",
    "description": "Prometheus labels routers by netom's ingress id; the netom API (Infinity) supplies the sysName.",
    "datasource": MIXED, "gridPos": {"x": 0, "y": y, "w": 12, "h": 12},
    "targets": [
        t('netom_bmp_state_num_received_prefixes_total{component="bmp-in"}', ref="A", instant=True, fmt="table"),
        t('netom_bmp_state_num_up_peers_total{component="bmp-in"}', ref="B", instant=True, fmt="table"),
        {"datasource": API, "refId": "C", "type": "json", "source": "url", "format": "table",
         "parser": "backend",
         "url": "http://172.20.20.101:8080/api/v1/ingresses?filter[type]=bmp",
         "url_options": {"method": "GET", "data": ""}, "root_selector": "data",
         "columns": [{"selector": "id", "text": "router", "type": "string"},
                     {"selector": "name", "text": "Router", "type": "string"},
                     {"selector": "remote_addr", "text": "Mgmt IP", "type": "string"},
                     {"selector": "state", "text": "State", "type": "string"}],
         "filters": []},
    ],
    "transformations": [
        {"id": "joinByField", "options": {"byField": "router", "mode": "outer"}},
        {"id": "organize", "options": {
            "excludeByName": {"Time": True, "Time 1": True, "Time 2": True, "__name__": True,
                              "__name__ 1": True, "__name__ 2": True, "component": True,
                              "component 1": True, "component 2": True, "collector": True,
                              "collector 1": True, "collector 2": True, "instance": True,
                              "instance 1": True, "instance 2": True, "job": True, "job 1": True,
                              "job 2": True},
            "indexByName": {"Router": 0, "router": 1, "Mgmt IP": 2, "Value #A": 3, "Value #B": 4, "State": 5},
            "renameByName": {"router": "Ingress", "Value #A": "Prefixes received",
                             "Value #B": "BGP sessions up"}}},
        {"id": "sortBy", "options": {"sort": [{"field": "Prefixes received", "desc": True}]}},
    ],
    "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
                    "overrides": [{"matcher": {"id": "byName", "options": "Prefixes received"},
                                   "properties": [{"id": "custom.cellOptions",
                                                   "value": {"type": "gauge", "mode": "basic"}},
                                                  {"id": "color", "value": {"mode": "continuous-BlPu"}}]}]},
    "options": {"showHeader": True, "cellHeight": "sm"}})

ts("BMP in → stored in RIB: worst latency", [
    t(f'max(netom_rib_unit_e2e_duration_milliseconds{{{COLL}}}) or on() vector(0)', "worst peer view", "A"),
], 12, y, w=12, h=6, unit="ms", interp="linear",
   overrides=[color("worst peer view", "orange")],
   desc="netom reports, per peer view, how long the most recent prefix took from BMP receipt to being stored in the RIB (whole milliseconds, kept for 60 s after activity). This panel shows the slowest view. 0 means under 1 ms.")
y += 6
ts("Restream sessions", [
    t('netom_bmp_tcp_out_clients_connected_total{component="bmp-out"} - netom_bmp_tcp_out_clients_disconnected_total{component="bmp-out"}', "consumers connected", "A"),
    t('netom_bmp_tcp_out_active_dumps_total{component="bmp-out"}', "dumps in progress", "B"),
], 12, y, w=12, h=6, desc="netom's bmp-tcp-out (:11020).")
y += 6

# ---------------------------------------------------------------- Row 4
row("Cost: what does this take?", y); y += 1
ts("CPU (cores)", [
    t(f'sum(rate(container_cpu_usage_seconds_total{{{NETOM_C}}}[15s]))', "netom", "A"),
    t(f'sum(rate(container_cpu_usage_seconds_total{{{CONS_C}}}[15s]))', "consumers", "B"),
    t(f'sum(rate(container_cpu_usage_seconds_total{{{ROUTER_C}}}[15s]))', "all FRR routers", "C"),
], 0, y, w=12, h=8, interp="linear",
   overrides=[color("netom", "purple"), color("consumers", "green"), color("all FRR routers", "blue")])
ts("Memory", [
    t(f'sum(container_memory_working_set_bytes{{{NETOM_C}}})', "netom", "A"),
    t(f'sum(container_memory_working_set_bytes{{{CONS_C}}})', "consumers", "B"),
    t(f'sum(container_memory_working_set_bytes{{{ROUTER_C}}})', "all FRR routers", "C"),
], 12, y, w=12, h=8, unit="bytes", interp="linear",
   overrides=[color("netom", "purple"), color("consumers", "green"), color("all FRR routers", "blue")])

dash = {
    "uid": "netom-scale", "title": "netom at scale", "tags": ["bmp", "netom"],
    "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": True,
    "refresh": "2s", "time": {"from": "now-10m", "to": "now"},
    "timepicker": {"refresh_intervals": ["1s", "2s", "5s", "10s", "30s"]},
    "description": "Scale demo: how many routers and paths netom holds, how fast it ingests, and how fast a new consumer gets the full table.",
    "panels": panels, "templating": {"list": []}, "annotations": {"list": []},
}
json.dump(dash, open(sys.argv[1], "w"), indent=2)
print(len(panels), "panels")
