#!/usr/bin/env python3
"""Port Monitor — queries Zabbix API, serves port-map HTML page."""
import json, re, os
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

ZABBIX_URL  = os.environ.get("ZABBIX_URL",  "http://zabbix.dc.prod/api_jsonrpc.php")
ZABBIX_USER = os.environ.get("ZABBIX_USER", "dc")
ZABBIX_PASS = os.environ.get("ZABBIX_PASS", "xgGuLU94SX")

HOSTS = [
    {"hostid": "10454", "label": "Cisco ASR 1000", "ip": "172.16.2.200", "accent": "#2196F3"},
    {"hostid": "10643", "label": "Juniper MX204",  "ip": "172.16.2.105", "accent": "#FF9800"},
]

# Interfaces to skip (loopback / internal / management)
SKIP_RE = re.compile(
    r'^(Lo|Loopback|Tunnel|Tu|Vlan|Nu|Null|Vo|Virtual|irb|lo|fxp|jsrv|'
    r'pfh|pfe|lc|em|BDI|NV|Cr|Po|pime|__filtered__)', re.I)

def zbx(method, params, auth=None):
    body = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    headers = {"Content-Type": "application/json"}
    if auth:
        # Zabbix 6.4+ uses Bearer token in header; older versions used body["auth"]
        headers["Authorization"] = f"Bearer {auth}"
    req = urllib.request.Request(
        ZABBIX_URL, json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r).get("result")

def parse_iface(item_name):
    # Cisco:  Interface Te0/0/0(DESC): Speed
    m = re.match(r'Interface ([\w/\-]+)[\(:]', item_name)
    if m:
        return m.group(1)
    # Juniper: Interface [et-0/0/0][DESC]: Speed
    m = re.match(r'Interface \[([\w/\-\.]+)\]', item_name)
    if m:
        return m.group(1)
    return None

def speed_label(bps):
    bps = int(bps or 0)
    if bps >= 100_000_000_000: return "100G"
    if bps >= 40_000_000_000:  return "40G"
    if bps >= 25_000_000_000:  return "25G"
    if bps >= 10_000_000_000:  return "10G"
    if bps >= 1_000_000_000:   return "1G"
    if bps >= 100_000_000:     return "100M"
    return "?"

def port_class(bps):
    bps = int(bps or 0)
    if bps >= 40_000_000_000:  return "rect-lg"
    if bps >= 10_000_000_000:  return "sq-md"
    return "sq-sm"

def fetch_ports():
    try:
        auth = zbx("user.login", {"username": ZABBIX_USER, "password": ZABBIX_PASS})
        result = []
        for host_def in HOSTS:
            items = zbx("item.get", {
                "output": ["name", "lastvalue"],
                "hostids": [host_def["hostid"]],
                "search":  {"name": "Interface"},
                "limit":   2000
            }, auth)

            ports = {}
            for item in items:
                iface = parse_iface(item["name"])
                if not iface or "." in iface or SKIP_RE.match(iface):
                    continue

                if iface not in ports:
                    ports[iface] = {
                        "name": iface, "speed_bps": 0,
                        "op_status": 0, "errors": 0, "desc": ""
                    }

                val = item["lastvalue"] or "0"
                n   = item["name"]

                if ": Speed" in n:
                    try: ports[iface]["speed_bps"] = int(val)
                    except: pass
                elif "Operational status" in n:
                    try: ports[iface]["op_status"] = int(val)
                    except: pass
                elif re.search(r'(errors|discarded)', n, re.I):
                    try: ports[iface]["errors"] += int(float(val))
                    except: pass

                if not ports[iface]["desc"]:
                    dm = re.search(r'\(([^)]+)\)', n)
                    if dm:
                        ports[iface]["desc"] = dm.group(1)
                    dm2 = re.search(r'\]\[([^\]]+)\]', n)
                    if dm2:
                        ports[iface]["desc"] = dm2.group(1)

            # Ensure 4 x 100G ports exist for Juniper
            if host_def["hostid"] == "10643":
                for target_iface in ["et-0/0/0", "et-0/0/1", "et-0/0/2", "et-0/0/3"]:
                    if target_iface not in ports:
                        ports[target_iface] = {
                            "name": target_iface,
                            "speed_bps": 100_000_000_000,
                            "op_status": 2,
                            "errors": 0,
                            "desc": "Zarezerwowany 100G"
                        }
                    else:
                        ports[target_iface]["speed_bps"] = 100_000_000_000

            def get_status(p):
                s  = p["speed_bps"]
                op = p["op_status"]
                e  = p["errors"]
                if s == 0 and op == 0: return "disabled"
                if s == 0 or op == 2:  return "down"
                if e > 0:              return "errors"
                return "up"

            port_list = []
            for p in ports.values():
                p["status"]      = get_status(p)
                p["speed_label"] = speed_label(p["speed_bps"])
                p["shape_class"] = port_class(p["speed_bps"])
                port_list.append(p)

            port_list.sort(key=lambda x: (-x["speed_bps"], x["name"]))
            result.append({
                "label": host_def["label"],
                "ip":    host_def["ip"],
                "accent": host_def["accent"],
                "ports": port_list,
            })
        return result
    except Exception as e:
        return [{"error": str(e)}]


HTML = r"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Port Status — Network Infrastructure</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #0d1117;
  color: #e6edf3;
  font-family: 'Segoe UI', system-ui, sans-serif;
  padding: 20px;
  min-height: 100vh;
}
h1 {
  font-size: 1.3rem; font-weight: 600; color: #58a6ff;
  margin-bottom: 18px; display: flex; align-items: center; gap: 10px;
}
.refresh-info { font-size:.75rem; color:#8b949e; margin-left:auto; }
.router-wrap  { margin-bottom: 24px; }
.router-header { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.router-name   { font-size: .95rem; font-weight: 600; }
.router-ip     { font-size: .78rem; color: #8b949e; font-family: monospace; }
.chassis {
  background: linear-gradient(135deg, #161b22 0%, #1c2128 100%);
  border: 1px solid #30363d;
  border-radius: 10px;
  padding: 14px 18px;
  box-shadow: 0 4px 24px rgba(0,0,0,.5);
}
.speed-group { margin-bottom: 12px; }
.speed-group:last-child { margin-bottom: 0; }
.speed-label {
  font-size: .62rem; color: #8b949e; text-transform: uppercase;
  letter-spacing: .08em; margin-bottom: 5px; font-weight: 600;
}
.ports-row { display: flex; flex-wrap: wrap; gap: 5px; }

/* PORT SHAPES */
.port {
  position: relative; cursor: pointer; border-radius: 4px;
  transition: transform .15s, box-shadow .15s;
  display: flex; align-items: flex-end; justify-content: center;
  padding-bottom: 3px;
  border: 1px solid rgba(255,255,255,.08);
}
.port:hover {
  transform: translateY(-3px) scale(1.1);
  box-shadow: 0 6px 20px rgba(0,0,0,.7);
  z-index: 10;
}
.sq-sm  { width: 34px; height: 34px; }
.sq-md  { width: 40px; height: 40px; }
.rect-lg { width: 82px; height: 36px; }

.port-lbl {
  font-size: .48rem; font-weight: 700;
  color: rgba(255,255,255,.65); text-align: center;
  pointer-events: none; white-space: nowrap;
  overflow: hidden; max-width: 95%;
}
.port::before {
  content: '';
  position: absolute; top: 4px; left: 50%; transform: translateX(-50%);
  width: 6px; height: 6px; border-radius: 50%;
}
/* STATUS */
.port.up       { background: linear-gradient(180deg,#0e2a1b,#1a3d26); border-color:#2ea043; }
.port.up::before { background:#3fb950; box-shadow:0 0 5px #3fb950; }
.port.down     { background: linear-gradient(180deg,#2a0e0e,#3d1a1a); border-color:#da3633; }
.port.down::before { background:#f85149; box-shadow:0 0 5px #f85149; }
.port.errors   { background: linear-gradient(180deg,#2a1d0e,#3d2d1a); border-color:#d29922; }
.port.errors::before { background:#e3b341; box-shadow:0 0 5px #e3b341; }
.port.disabled { background: linear-gradient(180deg,#161b22,#1c2128); border-color:#21262d; opacity:.5; }
.port.disabled::before { background:#484f58; }

/* TOOLTIP */
.tooltip {
  display: none; position: fixed; z-index: 9999;
  background: #161b22; border: 1px solid #30363d;
  border-radius: 8px; padding: 10px 14px;
  font-size: .8rem; line-height: 1.65;
  pointer-events: none;
  box-shadow: 0 8px 32px rgba(0,0,0,.8);
  min-width: 180px; max-width: 270px;
}
.tooltip.vis { display: block; }
.tt-name { font-weight: 700; font-size:.9rem; margin-bottom:4px; }
.tt-row  { display:flex; justify-content:space-between; gap:14px; }
.tt-key  { color:#8b949e; }
.tt-val  { font-family:monospace; }
.up-c    { color:#3fb950; font-weight:600; }
.dn-c    { color:#f85149; font-weight:600; }
.er-c    { color:#e3b341; font-weight:600; }
.dis-c   { color:#8b949e; }

/* LEGEND */
.legend { display:flex; gap:16px; margin-top:18px; flex-wrap:wrap; align-items:center; }
.leg    { display:flex; align-items:center; gap:6px; font-size:.75rem; color:#8b949e; }
.ld     { width:12px; height:12px; border-radius:2px; }
.ld-up  { background:#2ea043; }
.ld-dn  { background:#da3633; }
.ld-er  { background:#d29922; }
.ld-dis { background:#21262d; border:1px solid #30363d; }
.shape-note { margin-left:auto; font-size:.7rem; color:#484f58; }
</style>
</head>
<body>
<h1>
  🖥 Network Port Status
  <span class="refresh-info" id="cd">Odświeżanie za 30s</span>
</h1>
<div id="app">__CONTENT__</div>
<div class="legend">
  <div class="leg"><div class="ld ld-up"></div>Link UP</div>
  <div class="leg"><div class="ld ld-dn"></div>Link DOWN</div>
  <div class="leg"><div class="ld ld-er"></div>Błędy pakietów</div>
  <div class="leg"><div class="ld ld-dis"></div>Port wyłączony</div>
  <span class="shape-note">1G/10G = kwadrat &nbsp;•&nbsp; 40G+ = prostokąt</span>
</div>
<div class="tooltip" id="tt"></div>
<script>
const tt = document.getElementById('tt');
document.querySelectorAll('.port').forEach(p => {
  p.addEventListener('mousemove', e => {
    tt.innerHTML = p.dataset.tip; tt.classList.add('vis');
    let x = e.clientX + 14, y = e.clientY + 14;
    if (x + 280 > window.innerWidth)  x = e.clientX - 284;
    if (y + 150 > window.innerHeight) y = e.clientY - 154;
    tt.style.left = x + 'px'; tt.style.top = y + 'px';
  });
  p.addEventListener('mouseleave', () => tt.classList.remove('vis'));
});
let s = 30;
const cd = document.getElementById('cd');
setInterval(() => { s--; cd.textContent = 'Odświeżanie za ' + s + 's'; if (s<=0) location.reload(); }, 1000);
</script>
</body>
</html>"""


def build_tip(p):
    sc = {"up": "up-c", "down": "dn-c", "errors": "er-c", "disabled": "dis-c"}
    st = {"up": "🟢 UP", "down": "🔴 DOWN", "errors": "🟠 Errors", "disabled": "⚫ Disabled"}
    status = p["status"]
    desc   = (p.get("desc") or "").replace('"', '').replace("'", "")[:40]
    err    = p.get("errors", 0)
    h  = f'<div class="tt-name">{p["name"]}</div>'
    if desc:
        h += f'<div class="tt-row"><span class="tt-key">Opis</span><span class="tt-val">{desc}</span></div>'
    h += f'<div class="tt-row"><span class="tt-key">Prędkość</span><span class="tt-val">{p["speed_label"]}</span></div>'
    h += f'<div class="tt-row"><span class="tt-key">Status</span><span class="{sc[status]}">{st[status]}</span></div>'
    if err:
        h += f'<div class="tt-row"><span class="tt-key">Błędy</span><span class="er-c">{err:,}</span></div>'
    return h.replace('"', '&quot;')


def build_html(data):
    content = ""
    for router in data:
        if "error" in router:
            content += f'<p style="color:#f85149;padding:12px">Błąd: {router["error"]}</p>'
            continue

        groups = {}
        order  = ["100G", "40G", "25G", "10G", "1G", "100M", "?"]
        for sl in order:
            groups[sl] = []
        for p in router["ports"]:
            sl = p["speed_label"]
            groups.setdefault(sl, []).append(p)

        chassis = ""
        for sl in order:
            pts = groups.get(sl, [])
            if not pts:
                continue
            row = ""
            for p in pts:
                tip   = build_tip(p)
                name  = p["name"]
                short = re.sub(r'^(TenGig|GigabitEthernet|FastEthernet)', '', name)
                short = re.sub(r'(et-|xe-|ge-)', '', short)
                row += (f'<div class="port {p["shape_class"]} {p["status"]}" '
                        f'data-tip="{tip}">'
                        f'<span class="port-lbl">{short[:10]}</span></div>')
            chassis += (f'<div class="speed-group">'
                        f'<div class="speed-label">{sl}</div>'
                        f'<div class="ports-row">{row}</div></div>')

        content += f"""
<div class="router-wrap">
  <div class="router-header">
    <div class="router-name" style="color:{router['accent']}">{router['label']}</div>
    <div class="router-ip">{router['ip']}</div>
  </div>
  <div class="chassis">{chassis}</div>
</div>"""

    return HTML.replace("__CONTENT__", content)


COMPACT_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html {
  background: #111217;
  width: 100%;
  height: 100%;
}
body {
  background: #111217;
  color: #e6edf3;
  font-family: 'Segoe UI', system-ui, sans-serif;
  overflow: hidden;
  height: 100%;
  width: 100%;
  display: flex;
  flex-direction: column;
}
.header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 5px 8px 2px 8px;
  flex-shrink: 0;
}
.router-name { font-size: .85rem; font-weight: 600; white-space: nowrap; }
.router-ip   { font-size: .68rem; color: #8b949e; font-family: monospace; }

/* Outer wrapper fills all remaining height and width */
.ports-wrap {
  flex: 1 1 auto;
  display: flex;
  align-items: stretch;
  padding: 2px 6px 4px 6px;
  min-height: 0;
  width: 100%;
}

/* Speed groups go SIDE BY SIDE, each taking equal share of width */
.ports-row {
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  gap: 6px;
  width: 100%;
  align-items: stretch;
}

/* Each speed group fills its equal share */
.speed-grp {
  flex: 1 1 0;          /* equal width shares */
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.speed-lbl {
  font-size: .48rem; color: #8b949e;
  text-transform: uppercase; letter-spacing: .05em;
  flex-shrink: 0;
}

/* Ports inside group fill the group width */
.inner {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  flex: 1 1 auto;
  align-content: flex-start;
}

.port {
  flex: 1 1 auto;          /* stretch to fill row */
  min-height: 22px;
  max-height: 34px;
  position: relative; cursor: pointer; border-radius: 3px;
  border: 1px solid rgba(255,255,255,.1);
  transition: transform .12s, box-shadow .12s;
  display: flex; align-items: flex-end; justify-content: center;
  padding-bottom: 2px;
}
/* 1G / 10G: square-ish — max-width keeps them from getting too wide */
.sq-sm  { min-width: 20px; max-width: 44px; }
.sq-md  { min-width: 24px; max-width: 50px; }
/* 40G+: twice as wide */
.rect-lg{ min-width: 44px; max-width: 90px; flex: 2 1 auto; }

.port:hover { transform: scale(1.12); z-index: 50; box-shadow: 0 4px 14px rgba(0,0,0,.7); }
.port-lbl {
  font-size: .34rem; font-weight: 700; color: rgba(255,255,255,.55);
  pointer-events: none; white-space: nowrap; overflow: hidden;
  max-width: 95%; text-align: center;
}
.port::before {
  content: ''; position: absolute; top: 3px; left: 50%;
  transform: translateX(-50%); width: 5px; height: 5px; border-radius: 50%;
}
.port.up       { background: linear-gradient(180deg,#0e2a1b,#1a3d26); border-color: #2ea043; }
.port.up::before { background: #3fb950; box-shadow: 0 0 5px #3fb950; }
.port.down     { background: linear-gradient(180deg,#2a0e0e,#3d1a1a); border-color: #da3633; }
.port.down::before { background: #f85149; box-shadow: 0 0 5px #f85149; }
.port.errors   { background: linear-gradient(180deg,#2a1d0e,#3d2d1a); border-color: #d29922; }
.port.errors::before { background: #e3b341; box-shadow: 0 0 5px #e3b341; }
.port.disabled { background: linear-gradient(180deg,#161b22,#1c2128); border-color: #21262d; opacity: .4; }
.port.disabled::before { background: #484f58; }
.tt {
  display: none; position: fixed; z-index: 9999;
  background: #0d1117; border: 1px solid #30363d;
  border-radius: 6px; padding: 8px 12px;
  font-size: .75rem; line-height: 1.6; pointer-events: none;
  box-shadow: 0 6px 24px rgba(0,0,0,.9);
  min-width: 160px; max-width: 240px; color: #e6edf3;
}
.tt.vis { display: block; }
.tt-name { font-weight: 700; font-size: .82rem; margin-bottom: 3px; }
.tt-row  { display: flex; justify-content: space-between; gap: 12px; }
.tt-key  { color: #8b949e; }
.tt-val  { font-family: monospace; }
.up-c  { color: #3fb950; font-weight: 600; }
.dn-c  { color: #f85149; font-weight: 600; }
.er-c  { color: #e3b341; font-weight: 600; }
.dis-c { color: #8b949e; }
"""

COMPACT_JS = """
const tt = document.getElementById('tt');
document.querySelectorAll('.port').forEach(p => {
  p.addEventListener('mousemove', e => {
    tt.innerHTML = p.dataset.tip; tt.classList.add('vis');
    let x = e.clientX+10, y = e.clientY+10;
    if (x+250>window.innerWidth)  x=e.clientX-254;
    if (y+140>window.innerHeight) y=e.clientY-144;
    tt.style.left=x+'px'; tt.style.top=y+'px';
  });
  p.addEventListener('mouseleave',()=>tt.classList.remove('vis'));
});
setTimeout(()=>location.reload(), 30000);
"""


def build_compact(router_data, label="", ip="", accent="#58a6ff"):
    """Full dark HTML panel for one router — embeds in Grafana iframe (no white bg)."""
    if "error" in router_data:
        return (f'<!DOCTYPE html><html><head><meta charset="UTF-8">'
                f'<style>html,body{{background:#111217;color:#f85149;font-family:sans-serif;padding:8px}}</style></head>'
                f'<body>Błąd: {router_data.get("error","?")} </body></html>')

    order  = ["100G", "40G", "25G", "10G", "1G", "100M"]
    groups = {sl: [] for sl in order}
    for p in router_data.get("ports", []):
        # Skip ports with unknown speed (no SNMP data)
        if p["speed_label"] == "?":
            continue
        groups.setdefault(p["speed_label"], []).append(p)

    inner = ""
    for sl in order:
        pts = groups.get(sl, [])
        if not pts:
            continue
        row = ""
        for p in pts:
            tip   = build_tip(p)
            short = re.sub(r'^(TenGig|GigabitEthernet|FastEthernet)', '', p["name"])
            short = re.sub(r'(et-|xe-|ge-)', '', short)
            row += (f'<div class="port {p["shape_class"]} {p["status"]}" data-tip="{tip}">'
                    f'<span class="port-lbl">{short[:8]}</span></div>')
        inner += (f'<div class="speed-grp">'
                  f'<div class="speed-lbl">{sl}</div>'
                  f'<div class="inner">{row}</div></div>')

    hdr = (f'<div class="header">'
           f'<span class="router-name" style="color:{accent}">{label}</span>'
           f'<span class="router-ip">{ip}</span>'
           f'</div>') if label else ""

    return (f'<!DOCTYPE html><html><head><meta charset="UTF-8">'
            f'<style>{COMPACT_CSS}</style></head>'
            f'<body>{hdr}<div class="ports-wrap"><div class="ports-row">{inner}</div></div>'
            f'<div class="tt" id="tt"></div>'
            f'<script>{COMPACT_JS}</script></body></html>')


STATUS_FILL = {
    "up":       ("#1a3d26", "#2ea043", "#3fb950"),
    "down":     ("#3d1a1a", "#da3633", "#f85149"),
    "errors":   ("#3d2d1a", "#d29922", "#e3b341"),
    "disabled": ("#1c2128", "#21262d", "#484f58"),
}

def build_svg(router_data, label="", ip="", accent="#58a6ff"):
    """Generate SVG port map — scales to any width via viewBox."""
    ORDER = ["100G", "40G", "25G", "10G", "1G", "100M"]
    groups = {sl: [] for sl in ORDER}
    for p in router_data.get("ports", []):
        if p["speed_label"] == "?":
            continue
        groups.setdefault(p["speed_label"], []).append(p)
    active = [(sl, groups[sl]) for sl in ORDER if groups.get(sl)]

    # Layout constants (SVG units)
    HDR_H   = 22   # header row height
    LBL_H   = 10   # speed label height
    PORT_H  = 26   # port rect height
    GAP     = 4    # gap between groups and ports
    GRP_W   = 0    # computed per group
    TOTAL_H = HDR_H + LBL_H + PORT_H + GAP * 2

    # How many ports per group
    n_groups = len(active)
    if n_groups == 0:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 40"><text x="8" y="20" fill="#8b949e" font-size="12">No data</text></svg>'

    VB_W = 800  # viewBox width (fixed — img scales this)
    grp_w = (VB_W - GAP * (n_groups + 1)) // n_groups

    els = []
    # Background
    els.append(f'<rect width="{VB_W}" height="{TOTAL_H}" fill="#111217"/>')
    # Header
    els.append(f'<text x="8" y="15" font-size="13" font-weight="600" fill="{accent}" font-family="system-ui,sans-serif">{label}</text>')
    els.append(f'<text x="{8 + len(label)*8}" y="15" font-size="10" fill="#8b949e" font-family="monospace">{ip}</text>')

    x = GAP
    for sl, ports in active:
        # Speed label
        els.append(f'<text x="{x}" y="{HDR_H + LBL_H - 1}" font-size="8" fill="#8b949e" '
                   f'font-family="system-ui,sans-serif" text-anchor="start">{sl}</text>')

        n = len(ports)
        if n == 0:
            x += grp_w + GAP
            continue

        pw = max(18, min(50, (grp_w - GAP * (n - 1)) // n))  # port width
        if sl in ("100G", "40G"):
            pw = min(80, pw * 2)  # wider for high-speed

        px = x
        for p in ports:
            fill, stroke, led = STATUS_FILL.get(p["status"], STATUS_FILL["disabled"])
            tip = p["name"]
            if p.get("desc"): tip += f" | {p['desc']}"
            tip += f" | {p['speed_label']} | {p['status'].upper()}"
            if p.get("errors"): tip += f" | Err:{p['errors']}"
            short = re.sub(r'^(TenGig|GigabitEthernet|FastEthernet|et-|xe-|ge-)', '', p["name"])
            els.append(
                f'<rect x="{px}" y="{HDR_H + LBL_H}" width="{pw}" height="{PORT_H}" rx="3" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="1">'
                f'<title>{tip}</title></rect>'
                f'<circle cx="{px + pw//2}" cy="{HDR_H + LBL_H + 5}" r="3" fill="{led}"/>'
                f'<text x="{px + pw//2}" y="{HDR_H + LBL_H + PORT_H - 3}" font-size="6" '
                f'fill="rgba(255,255,255,0.55)" text-anchor="middle" font-family="system-ui,sans-serif">'
                f'{short[:7]}</text>'
            )
            px += pw + GAP
        x += grp_w + GAP

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VB_W} {TOTAL_H}" '
        f'preserveAspectRatio="xMinYMid meet">'
        + "".join(els) +
        "</svg>"
    )
    return svg


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send_html(self, body_bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Frame-Options", "ALLOWALL")
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            data = fetch_ports()
            self.send_html(build_html(data).encode())
        elif self.path == "/asr":
            data = fetch_ports()
            router = next((r for r in data if "Cisco" in r.get("label","")), data[0] if data else {})
            self.send_html(build_compact(router,
                label=router.get("label",""), ip=router.get("ip",""),
                accent=router.get("accent","#2196F3")).encode())
        elif self.path == "/juniper":
            data = fetch_ports()
            router = next((r for r in data if "Juniper" in r.get("label","")), data[-1] if data else {})
            self.send_html(build_compact(router,
                label=router.get("label",""), ip=router.get("ip",""),
                accent=router.get("accent","#FF9800")).encode())
        elif self.path == "/api/data":
            data = fetch_ports()
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/asr.svg", "/juniper.svg"):
            data = fetch_ports()
            key = "Cisco" if "asr" in self.path else "Juniper"
            acc = "#2196F3" if "asr" in self.path else "#FF9800"
            router = next((r for r in data if key in r.get("label","")), {})
            svg = build_svg(router,
                label=router.get("label",""), ip=router.get("ip",""), accent=acc)
            body = svg.encode()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8090))
    print(f"Port Monitor starting on :{port} …")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
