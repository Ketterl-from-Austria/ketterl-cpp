"""
gen_villa_real.py  —  CPP Villa Echtdaten → cpp_villa_real.html
Liest knx_config_cpp.json und baut eine vollständige HTML-Visualisierung
im Stil des cpp_villa_demo.html (Zetterl / Architektur / Enterprise / Dashboard).
"""
import json, re, sys, os
from collections import defaultdict

# ── Input / Output ────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_IN  = os.path.join(SCRIPT_DIR, "knx_config_cpp.json")
HTML_OUT = os.path.join(SCRIPT_DIR, "..", "cpp_villa_real.html")   # Ketterl-Root

# ── Stockwerk-Reihenfolge & Abkürzungen ───────────────────────────────────────
FLOOR_ORDER = ["KG", "EG", "OG", "DG", "AUSSEN", "UG", "_ZE"]
FLOOR_ABBR  = {"KG":"KG","EG":"EG","OG":"OG","DG":"DG","AUSSEN":"Au.","UG":"UG","_ZE":"Zen."}
FLOOR_LABEL = {"KG":"Keller","EG":"Erdgeschoss","OG":"Obergeschoss",
               "DG":"Dachgeschoss","AUSSEN":"Außenbereich","UG":"Untergeschoss",
               "_ZE":"Zentral"}

# ── Gebäude ───────────────────────────────────────────────────────────────────
# Beim CPP gibt es nur ein Gebäude; AUSSEN als separate "Gebäudeeinheit"
GEBAEUDE = [
    {"id":"haupthaus", "name":"CPP Villa",     "short":"Villa"},
    {"id":"aussen",    "name":"Außenbereich",  "short":"Außen."},
]

# ── Türen / Verbindungen (logisch abgeleitet aus dem Grundriss) ───────────────
# Hubs = Verbindungsräume pro Stockwerk
FLOOR_HUBS = {
    "KG":     "KG_Technik-Gira",
    "EG":     "EG_Gang",
    "OG":     "OG_Gang-OG",
    "DG":     "DG_Gang-DG",
    "AUSSEN": "AUSSEN_Garten",
    "UG":     None,
}
# Zusätzliche direkte Verbindungen
EXTRA_DOORS = {
    "EG_Eingang":         ["EG_Gang", "EG_Windfang"],
    "EG_Windfang":        ["EG_Eingang", "EG_Gang"],
    "EG_Gang":            ["EG_Treppenhaus","EG_Büro","EG_WC","EG_Bad-WC",
                           "EG_Wellness","EG_Küche","EG_Wohnzimmer","EG_Abstellraum"],
    "EG_Treppenhaus":     ["EG_Gang","OG_Treppenhaus-OG"],
    "OG_Treppenhaus-OG":  ["EG_Treppenhaus","OG_Gang-OG"],
    "OG_Gang-OG":         ["OG_Treppenhaus-OG","OG_Kinderzimmer-01","OG_Kinderzimmer-02",
                            "OG_Kinderzimmer-03","OG_Kinderzimmer-04","OG_Bad-OG",
                            "OG_HWR","OG_Chillout-Zone","OG_Ankleide","OG_Garderobe","OG_Garage"],
    "DG_Gang-DG":         ["DG_Schlafzimmer","DG_Bad-DG","DG_Ankleide-DG",
                            "DG_Gästezimmer","DG_Terrasse-DG","DG_Serverraum-DG"],
    "EG_Wellness":        ["EG_Gang","EG_Terrasse-EG"],
    "EG_Terrasse-EG":     ["EG_Wellness","AUSSEN_Grill-Platz","AUSSEN_Pool-Terrasse"],
    "AUSSEN_Gartenpool":  ["AUSSEN_Pool-Terrasse","AUSSEN_Pool-Technik","AUSSEN_Sportpool"],
    "AUSSEN_Sportpool":   ["AUSSEN_Pool-Terrasse","AUSSEN_Pool-Technik","AUSSEN_Gartenpool"],
    "AUSSEN_Pool-Terrasse":["EG_Terrasse-EG","AUSSEN_Gartenpool","AUSSEN_Sportpool"],
    "KG_Technik-Gira":    ["KG_Fritzl-Keller","KG_Heizung-KG","KG_Keller","KG_Technik-KG"],
    "EG_Wohnzimmer":      ["EG_Gang","EG_Küche","EG_Terrasse-EG"],
    "EG_Küche":           ["EG_Gang","EG_Wohnzimmer","EG_Hauswirtschaft"],
}

# ── Device-Typ-Erkennung aus KNX-Daten ────────────────────────────────────────
def detect_type(dev):
    func = dev.get("func","")
    sfs  = {a["subfunc"] for a in dev.get("addresses",[])}
    if func == "temperature":                          return "display", "°C"
    if func == "blind":                                return "dimmer",  "%"
    if "dimming" in sfs:                               return "dimmer",  "%"
    if func in ("light","socket","heating") and "switch" in sfs: return "switch", ""
    if func == "light" and "feedback" in sfs:          return "switch", ""
    if func == "other"  and "switch" in sfs:           return "switch", ""
    return None, ""

def main_ga(dev):
    """Primäre KNX-Adresse (Switch > erster Eintrag)."""
    for a in dev.get("addresses",[]):
        if a["subfunc"] == "switch": return a["ga"]
    return dev["addresses"][0]["ga"] if dev.get("addresses") else "—"

def js_str(s):
    return s.replace("\\","\\\\").replace("'","\\'")

# ── JSON laden ────────────────────────────────────────────────────────────────
print(f"Lese {JSON_IN} …")
with open(JSON_IN, encoding="utf-8") as f:
    data = json.load(f)

rooms_raw = data["rooms"]
print(f"  → {len(rooms_raw)} Räume, {data['meta']['total_gas']} Gruppenadressen")

# ── DEVICES & ROOMS aufbauen ──────────────────────────────────────────────────
devices_js   = {}   # dev_id → {name, type, knx, unit, value}
rooms_out    = []   # Liste der ROOMS-Einträge

def room_gebaeude(floor):
    return "aussen" if floor == "AUSSEN" else "haupthaus"

for room in rooms_raw:
    rid   = room["id"]
    rname = room["name"]
    floor = room["floor"]
    geb   = room_gebaeude(floor)
    devs  = room.get("devices", {})

    ctrl_list   = []
    has_content = False

    for dev_id, dev in devs.items():
        dtype, unit = detect_type(dev)
        if dtype is None:
            continue
        label   = dev.get("label") or dev.get("name_prefix","?")
        # Kürze zu lange Labels
        label = re.sub(r"^(?:HA |EG |OG |DG |KG |AUSSEN )\s*","", label).strip()
        if len(label) > 40: label = label[:38]+"…"

        ga      = main_ga(dev)
        val_map = {"switch": False, "dimmer": 50, "display": "—"}
        dval    = val_map.get(dtype, False)

        devices_js[dev_id] = {"name": label, "type": dtype, "knx": ga, "unit": unit, "value": dval}
        ctrl_list.append({"device": dev_id, "alias": label})
        has_content = True

    if not has_content:
        # Raum hat keine interpretierbaren Geräte → trotzdem eintragen (leere Seite)
        rooms_out.append({
            "id": rid, "name": rname, "floor": floor,
            "gebaeude": geb, "type": "room",
            "controls": [], "doors": []
        })
        continue

    # Türen berechnen
    doors = set()
    hub   = FLOOR_HUBS.get(floor)
    if hub and hub != rid:
        doors.add(hub)
    for xid in EXTRA_DOORS.get(rid, []):
        doors.add(xid)
    # Rücklinks: wenn ein anderer Raum mich als Tür hat → ich ihn auch
    for other_id, targets in EXTRA_DOORS.items():
        if rid in targets and other_id != rid:
            doors.add(other_id)

    rooms_out.append({
        "id":       rid,
        "name":     rname,
        "floor":    floor,
        "gebaeude": geb,
        "type":     "room",
        "controls": ctrl_list,
        "doors":    sorted(doors),
    })

# Pro Stockwerk eine Zimmerliste als erste Seite des Geschosses einfügen
rooms_by_floor = defaultdict(list)
for r in rooms_out:
    rooms_by_floor[r["floor"]].append(r)

rooms_with_lists = []
for floor in FLOOR_ORDER:
    if not rooms_by_floor[floor]:
        continue
    geb = "aussen" if floor == "AUSSEN" else "haupthaus"
    rooms_with_lists.append({
        "id":       f"list_{floor}",
        "name":     FLOOR_LABEL.get(floor, floor),
        "floor":    floor,
        "gebaeude": geb,
        "type":     "floorlist",
        "doors":    [],
    })
    rooms_with_lists.extend(rooms_by_floor[floor])

rooms_out = rooms_with_lists

print(f"  → {len(devices_js)} Devices, {len(rooms_out)} Räume (inkl. Übersicht)")

# ── JS-Daten rendern ──────────────────────────────────────────────────────────
def render_devices():
    lines = []
    for did, d in devices_js.items():
        v = "false" if d["type"]=="switch" else (f"'{js_str(str(d['value']))}'" if d["type"]=="display" else str(d["value"]))
        lines.append(
            f"  {json.dumps(did)}: {{ name:{json.dumps(d['name'])}, type:{json.dumps(d['type'])}, "
            f"knx:{json.dumps(d['knx'])}, unit:{json.dumps(d['unit'])}, value:{v} }},"
        )
    return "\n".join(lines)

def render_rooms():
    parts = []
    for r in rooms_out:
        ctrl_s = ""
        if r.get("controls"):
            items = ",\n      ".join(
                f"{{ device:{json.dumps(c['device'])}, alias:{json.dumps(c['alias'])} }}"
                for c in r["controls"]
            )
            ctrl_s = f"\n    controls:[\n      {items}\n    ],"
        doors_s = ""
        if r.get("doors"):
            # Nur Türen zu existierenden Räumen
            valid_ids = {rx["id"] for rx in rooms_out}
            valid = [d for d in r["doors"] if d in valid_ids]
            if valid:
                doors_s = "doors:[" + ",".join(f"'{js_str(d)}'" for d in valid) + "]"
            else:
                doors_s = "doors:[]"
        else:
            doors_s = "doors:[]"
        parts.append(
            f"  {{ id:{json.dumps(r['id'])}, name:{json.dumps(r['name'])}, "
            f"floor:{json.dumps(r['floor'])}, gebaeude:{json.dumps(r['gebaeude'])}, "
            f"type:{json.dumps(r['type'])},{ctrl_s} {doors_s} }}"
        )
    return ",\n".join(parts)

# ── HTML-Template ─────────────────────────────────────────────────────────────
# CSS/JS identisch mit cpp_villa_demo.html — GEBAEUDE + FLOOR_ORDER angepasst
GEBAEUDE_JS    = json.dumps(GEBAEUDE)
FLOOR_ORDER_JS = json.dumps(FLOOR_ORDER)
FLOOR_ABBR_JS  = json.dumps(FLOOR_ABBR)
FLOOR_LABEL_JS = json.dumps(FLOOR_LABEL)

html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#c8c2b0">
<title>CPP Villa – Steuerung (Echtdaten)</title>
<link href="https://fonts.googleapis.com/css2?family=Caveat:wght@400;600;700&display=swap" rel="stylesheet">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }}
:root {{
  --paper: #faf9f0; --ink: #1c1c2e; --ink-light: #5a5a7a;
  --blue-line: rgba(80,110,200,0.16); --red-margin: rgba(210,50,50,0.35);
  --yellow: rgba(255,218,40,0.55); --door: #8B6410; --font: 'Caveat', cursive;
}}
body {{
  background: #c8c2b0;
  background-image: radial-gradient(ellipse at 15% 25%, rgba(160,140,100,.4) 0%, transparent 55%),
                    radial-gradient(ellipse at 85% 75%, rgba(140,120,90,.3) 0%, transparent 55%);
  min-height: 100dvh; overflow-x: hidden;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 16px 8px; font-family: var(--font);
}}
#stage-container {{ display: flex; align-items: flex-start; justify-content: center; }}
.stage-wrap {{ display: flex; flex-direction: column; align-items: center; gap: 8px; }}
.stage {{
  position: relative;
  width: min(460px, 96vw);
  height: min(680px, 88dvh);
}}
@media (min-width: 600px) {{ .stage {{ width: min(540px, 90vw); height: min(740px, 86dvh); }} }}
@media (min-width: 1100px) {{ .stage {{ width: min(600px, 44vw); height: min(840px, 84dvh); }} body {{ font-size: 18px; }} }}
@media (orientation: landscape) and (max-height: 520px) {{ .stage {{ width: min(68vw, 560px); height: min(92dvh, 460px); }} body {{ flex-direction: row; }} }}
body.mode-tv .stage {{ width: 98vw; height: 97dvh; max-width: none; }} body.mode-tv {{ font-size: 22px; }}
body.mode-bad .stage {{ width: 98vw; height: 97dvh; max-width: none; }} body.mode-bad {{ font-size: 26px; }}
.zettel {{
  position: absolute; inset: 0; background-color: var(--paper);
  border-radius: 2px;
  box-shadow: 4px 4px 14px rgba(0,0,0,.32), 0 1px 3px rgba(0,0,0,.15);
  background-image: linear-gradient(var(--blue-line) 1px, transparent 1px),
                    linear-gradient(90deg, var(--blue-line) 1px, transparent 1px);
  background-size: 26px 26px;
  display: flex; flex-direction: column; overflow: hidden;
  will-change: transform, opacity;
  transition: transform 0.32s cubic-bezier(.4,0,.2,1), opacity 0.32s;
}}
.zettel::before {{
  content: ''; position: absolute; left: 54px; top: 0; bottom: 0;
  width: 1.5px; background: var(--red-margin); pointer-events: none; z-index: 1;
}}
.clip {{ position: absolute; top: -9px; left: 50%; transform: translateX(-50%); width: 44px; height: 17px; background: linear-gradient(to bottom, #aaa, #888); border-radius: 3px 3px 1px 1px; z-index: 30; box-shadow: 0 2px 5px rgba(0,0,0,.35); }}
.clip::after {{ content: ''; position: absolute; top: 5px; left: 5px; right: 5px; height: 5px; background: #bbb; border-radius: 2px; }}
.z-left-col {{
  position: absolute; left: 0; top: 0; bottom: 0; width: 56px; z-index: 20;
  display: flex; flex-direction: column; align-items: center;
  padding: 10px 0 10px; gap: 5px;
}}
.nav-open-btn {{
  background: none; border: 1.5px solid transparent;
  font-size: 1.35rem; cursor: pointer; color: var(--ink-light);
  font-family: var(--font); padding: 2px 5px; border-radius: 3px; line-height: 1;
  transition: border-color .15s, background .15s; flex-shrink: 0; margin-bottom: 4px;
}}
.nav-open-btn:hover, .nav-open-btn:active {{ border-color: var(--ink-light); background: rgba(0,0,0,.06); }}
.z-floor-tab-l {{
  width: 42px; padding: 4px 5px;
  font-family: var(--font); font-size: 0.9rem; text-align: center;
  color: var(--ink-light); cursor: pointer;
  background: rgba(255,255,255,.4);
  border: 1.8px solid rgba(0,0,0,.28);
  border-radius: 3px; user-select: none; line-height: 1.2;
  transition: background .15s, color .15s;
  filter: url(#sketchy);
}}
.z-floor-tab-l.active {{ background: var(--yellow); color: var(--ink); font-weight: 700; border-color: var(--ink); }}
.z-floor-tab-l:not(.active):hover {{ background: rgba(255,255,255,.7); }}
.z-gebaeude-tabs {{ display: flex; gap: 3px; padding: 8px 14px 4px 68px; flex-shrink: 0; position: relative; z-index: 3; flex-wrap: wrap; }}
.z-gebaeude-tab {{
  padding: 3px 9px;
  border: 1.8px solid var(--ink); border-radius: 4px;
  font-family: var(--font); font-size: .85rem; cursor: pointer;
  color: var(--ink-light); background: rgba(200,195,180,.45);
  transition: background .12s, color .12s;
  filter: url(#sketchy);
}}
.z-gebaeude-tab.active {{ background: var(--yellow); color: var(--ink); font-weight: 700; }}
.z-gebaeude-tab:not(.active):hover {{ background: rgba(220,215,195,.7); }}
.z-head {{ padding: 6px 14px 6px 68px; flex-shrink: 0; position: relative; z-index: 2; }}
.z-floor {{ font-size: .9rem; color: var(--ink-light); }}
.z-title {{ font-size: 1.9rem; font-weight: 700; color: var(--ink); line-height: 1.1; border-bottom: 2.5px solid var(--ink); display: inline-block; padding-bottom: 2px; }}
.z-body {{ flex: 1; overflow-y: auto; padding: 8px 14px 10px 68px; position: relative; z-index: 2; -webkit-overflow-scrolling: touch; }}
.z-body::-webkit-scrollbar {{ width: 3px; }}
.z-body::-webkit-scrollbar-thumb {{ background: rgba(0,0,0,.15); border-radius: 2px; }}
.section-title {{ font-size: 1rem; color: var(--ink-light); font-weight: 600; text-decoration: underline; margin: 10px 0 4px; }}
.ctrl {{ display: flex; align-items: center; gap: 10px; padding: 5px 0; cursor: pointer; border-radius: 4px; transition: background .1s; user-select: none; }}
.ctrl:active {{ background: rgba(0,0,0,.04); }}
.cb {{
  width: 26px; height: 26px; flex-shrink: 0;
  border: 2.5px solid var(--ink); border-radius: 2px;
  position: relative; background: white;
  clip-path: polygon(1% 2%, 99% 0%, 98% 97%, 2% 99%);
  filter: url(#sketchy);
}}
.cb::after {{ content: '✗'; position: absolute; inset: -3px; display: flex; align-items: center; justify-content: center; font-size: 1.4rem; font-weight: 700; color: var(--ink); opacity: 0; transition: opacity .12s; }}
.ctrl.on .cb {{ background: var(--yellow); }}
.ctrl.on .cb::after {{ opacity: 1; }}
.ctrl-label {{ font-size: 1.25rem; color: var(--ink); flex: 1; line-height: 1.2; }}
.ctrl.on .ctrl-label {{ color: var(--ink-light); }}
.ctrl-addr {{ font-size: .72rem; color: rgba(0,0,0,.22); font-style: italic; cursor: default; user-select: text; border: 1px solid transparent; border-radius: 3px; padding: 1px 4px; transition: color .15s, border-color .15s; }}
.ctrl-addr:hover {{ color: rgba(0,0,0,.5); border-color: rgba(0,0,0,.15); }}
.dimmer-row {{ margin: 7px 0; }}
.dimmer-label {{ font-size: 1.25rem; color: var(--ink); display: flex; justify-content: space-between; margin-bottom: 3px; }}
.dimmer-val {{
  font-weight: 700; background: var(--yellow); padding: 0 7px;
  border-radius: 3px; border: 1.8px solid var(--ink);
  filter: url(#sketchy);
}}
input[type=range] {{ -webkit-appearance: none; width: 100%; background: transparent; }}
input[type=range]::-webkit-slider-runnable-track {{ height: 3px; background: var(--ink); border-radius: 2px; }}
input[type=range]::-webkit-slider-thumb {{ -webkit-appearance: none; width: 24px; height: 24px; margin-top: -10px; border-radius: 50%; background: white; border: 2.5px solid var(--ink); box-shadow: 1px 1px 4px rgba(0,0,0,.2); cursor: pointer; }}
.value-row {{ display: flex; align-items: center; justify-content: space-between; margin: 5px 0; }}
.value-label {{ font-size: 1.25rem; color: var(--ink); flex: 1; }}
.value-badge {{
  font-size: 1.25rem; font-weight: 700;
  background: var(--yellow);
  border: 2px solid var(--ink);
  padding: 1px 10px; border-radius: 4px; min-width: 70px; text-align: center;
  clip-path: polygon(1% 3%, 99% 0%, 98% 97%, 2% 99%);
  filter: url(#sketchy);
}}
hr.z-hr {{ border: none; border-top: 1.5px dashed rgba(0,0,0,.18); margin: 8px 0; }}
.z-doors {{ flex-shrink: 0; padding: 7px 14px 12px 68px; border-top: 2px dashed rgba(0,0,0,.13); position: relative; z-index: 2; }}
.doors-label {{ font-size: .9rem; color: var(--ink-light); margin-bottom: 5px; }}
.doors-row {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.door-btn {{
  display: inline-flex; align-items: center; gap: 5px;
  padding: 5px 12px;
  border: 2px solid var(--door); border-radius: 5px;
  background: rgba(255,255,255,.55); color: var(--door);
  font-family: var(--font); font-size: 1.1rem; cursor: pointer;
  clip-path: polygon(0.5% 3%, 99.5% 0%, 99% 97%, 1% 100%);
  filter: url(#sketchy);
  transition: background .15s;
}}
.door-btn:active {{ background: rgba(255,220,120,.5); }}
.floorplan-wrap {{ padding: 4px 14px 4px 68px; display: flex; align-items: center; justify-content: center; flex: 1; }}
.fp-svg {{ width: 100%; max-height: 100%; }}
.floorlist-grid {{ display: flex; flex-direction: column; padding: 2px 0 8px; }}
.floorlist-btn {{
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  padding: 7px 4px;
  border: none; border-bottom: 1.5px solid var(--blue-line);
  background: transparent; width: 100%;
  font-family: var(--font); cursor: pointer; text-align: left;
  transition: background .12s;
}}
.floorlist-btn:active {{ background: var(--yellow); }}
.floorlist-name {{ font-size: 1.2rem; color: var(--ink); }}
.floorlist-count {{ font-size: .8rem; color: var(--ink-light); white-space: nowrap; }}
.yt-wrap {{ margin: 10px auto 4px; border-radius: 3px; overflow: hidden;
  border: 2px solid var(--ink); aspect-ratio: 16/9; width: 70%; }}
.yt-wrap iframe {{ width: 100%; height: 100%; border: none; display: block; }}
.fp-room {{ fill: transparent; stroke: var(--ink); stroke-width: 2.5; cursor: pointer; transition: fill .18s; }}
.fp-room:hover, .fp-room:active {{ fill: var(--yellow); }}
.fp-txt {{ font-family: var(--font); font-size: 13px; fill: var(--ink); text-anchor: middle; dominant-baseline: middle; pointer-events: none; }}
.fp-door {{ stroke: var(--door); stroke-width: 1.5; fill: none; }}
.page-dots {{ display: flex; gap: 7px; align-items: center; padding: 4px 0; flex-wrap: wrap; justify-content: center; max-width: min(460px, 96vw); }}
.dot {{ width: 9px; height: 9px; border-radius: 50%; border: 1.5px solid rgba(0,0,0,.4); background: transparent; cursor: pointer; transition: background .18s; flex-shrink: 0; }}
.dot.cur {{ background: rgba(0,0,0,.75); border-color: rgba(0,0,0,.75); }}
.nav-btn {{ position: absolute; top: 50%; transform: translateY(-50%); z-index: 40; background: rgba(255,255,255,.75); border: 1.5px solid rgba(0,0,0,.2); border-radius: 50%; width: 38px; height: 38px; display: flex; align-items: center; justify-content: center; cursor: pointer; font-size: 1.1rem; color: var(--ink-light); transition: background .15s; }}
.nav-btn:active {{ background: white; }}
.btn-prev {{ left: -52px; }} .btn-next {{ right: -52px; }}
@media (max-width: 700px) {{ .nav-btn {{ display: none; }} }}
.swipe-tip {{ position: absolute; bottom: 12px; right: 14px; font-size: .8rem; color: rgba(0,0,0,.2); pointer-events: none; z-index: 5; }}
.nav-overlay {{ position: fixed; inset: 0; z-index: 200; background: rgba(0,0,0,.42); opacity: 1; transition: opacity .25s; }}
.nav-overlay.hidden {{ opacity: 0; pointer-events: none; }}
.nav-panel {{ position: absolute; left: 0; top: 0; bottom: 0; width: min(290px, 88vw); background-color: var(--paper); background-image: linear-gradient(var(--blue-line) 1px, transparent 1px), linear-gradient(90deg, var(--blue-line) 1px, transparent 1px); background-size: 26px 26px; box-shadow: 4px 0 20px rgba(0,0,0,.3); display: flex; flex-direction: column; transform: translateX(-100%); transition: transform .25s cubic-bezier(.4,0,.2,1); overflow: hidden; }}
.nav-overlay:not(.hidden) .nav-panel {{ transform: translateX(0); }}
.nav-panel::before {{ content: ''; position: absolute; left: 46px; top: 0; bottom: 0; width: 1.5px; background: var(--red-margin); pointer-events: none; }}
.nav-panel-head {{ display: flex; align-items: center; justify-content: space-between; padding: 14px 14px 10px 58px; border-bottom: 2px dashed rgba(0,0,0,.15); flex-shrink: 0; }}
.nav-panel-title {{ font-size: 1.5rem; font-weight: 700; color: var(--ink); border-bottom: 2px solid var(--ink); }}
.nav-close-btn {{ background: none; border: none; font-size: 1.3rem; cursor: pointer; color: var(--ink-light); font-family: var(--font); padding: 2px 6px; }}
.nav-list {{ flex: 1; overflow-y: auto; padding: 4px 0 20px; }}
.nav-gebaeude-label {{ font-size: 1rem; color: var(--ink); font-weight: 700; padding: 12px 14px 4px 58px; border-top: 1.5px solid rgba(0,0,0,.1); margin-top: 4px; }}
.nav-gebaeude-label:first-child {{ border-top: none; margin-top: 0; }}
.nav-floor-label {{ font-size: .82rem; color: var(--ink-light); padding: 6px 14px 2px 68px; font-weight: 600; text-transform: uppercase; letter-spacing: .07em; }}
.nav-room-item {{ padding: 5px 14px 5px 68px; font-size: 1.15rem; color: var(--ink); cursor: pointer; transition: background .12s; display: flex; align-items: center; gap: 6px; }}
.nav-room-item.cur {{ font-weight: 700; background: rgba(255,218,40,.3); }}
.nav-room-item:active {{ background: rgba(0,0,0,.06); }}
.nav-style-footer {{ flex-shrink: 0; border-top: 1.5px dashed rgba(0,0,0,.15); padding: 10px 14px 14px 14px; }}
.nav-style-title {{ font-size: .82rem; color: var(--ink-light); font-weight: 600; text-transform: uppercase; letter-spacing: .07em; margin-bottom: 7px; }}
.nav-style-btns {{ display: flex; flex-direction: column; gap: 6px; }}
.nav-style-btn {{
  display: flex; align-items: center; gap: 8px; padding: 5px 10px;
  background: rgba(255,255,255,.4); border: 1.5px solid rgba(0,0,0,.2); border-radius: 3px;
  font-family: var(--font); font-size: 1.05rem; color: var(--ink); cursor: pointer;
  text-align: left; transition: background .12s; filter: url(#sketchy);
}}
.nav-style-btn:hover {{ background: rgba(255,255,255,.7); }}
.nav-style-btn.active {{ background: var(--yellow); border-color: var(--ink); font-weight: 700; }}

/* ── ARCHITEKTUR ── */
body.style-architektur {{
  background: #e0ddd3;
  background-image:
    linear-gradient(rgba(80,95,155,0.22) 1px, transparent 1px),
    linear-gradient(90deg, rgba(80,95,155,0.22) 1px, transparent 1px),
    linear-gradient(rgba(120,135,180,0.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(120,135,180,0.08) 1px, transparent 1px);
  background-size: 40px 40px, 40px 40px, 4px 4px, 4px 4px;
}}
body.style-architektur .zettel {{
  background-color: #f5f3ec;
  background-image:
    linear-gradient(rgba(80,95,155,0.16) 1px, transparent 1px),
    linear-gradient(90deg, rgba(80,95,155,0.16) 1px, transparent 1px),
    linear-gradient(rgba(120,135,180,0.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(120,135,180,0.06) 1px, transparent 1px);
  background-size: 40px 40px, 40px 40px, 4px 4px, 4px 4px;
  box-shadow: 2px 2px 10px rgba(0,0,0,.16), 0 1px 3px rgba(0,0,0,.1); overflow: visible;
}}
body.style-architektur .zettel::before {{ display: none; }}
body.style-architektur .z-title {{ font-size: 1.55rem; font-weight: 600; color: #282835; border-bottom: 1px solid rgba(45,45,65,.45); }}
body.style-architektur .z-floor {{ color: #72728a; font-size: .83rem; }}
body.style-architektur .section-title {{ color: #72728a; text-decoration: none; border-bottom: 1px solid rgba(45,45,65,.28); padding-bottom: 2px; }}
body.style-architektur .ctrl-label {{ color: #282835; }}
body.style-architektur .ctrl-addr {{ color: rgba(60,60,80,.35); }}
body.style-architektur .value-label {{ color: #282835; }}
body.style-architektur .dimmer-label {{ color: #282835; }}
body.style-architektur .doors-label {{ color: #72728a; }}
body.style-architektur .swipe-tip {{ color: rgba(45,45,65,.22); }}
body.style-architektur hr.z-hr {{ border-top-color: rgba(45,45,65,.2); }}
body.style-architektur .cb {{ clip-path: none; filter: url(#pencil); border: 1.2px solid rgba(45,45,65,.8); border-radius: 0; background: rgba(245,243,236,.8); }}
body.style-architektur .ctrl.on .cb {{ background: rgba(195,190,165,.55); }}
body.style-architektur .cb::after {{ color: rgba(35,35,50,.9); }}
body.style-architektur .value-badge,
body.style-architektur .door-btn,
body.style-architektur .z-gebaeude-tab,
body.style-architektur .z-floor-tab-l,
body.style-architektur .dimmer-val,
body.style-architektur .nav-style-btn {{
  position: relative; clip-path: none; filter: url(#pencil); border: none; background: transparent; overflow: visible;
}}
body.style-architektur .value-badge::before, body.style-architektur .door-btn::before,
body.style-architektur .z-gebaeude-tab::before, body.style-architektur .z-floor-tab-l::before,
body.style-architektur .dimmer-val::before, body.style-architektur .nav-style-btn::before {{
  content: ''; position: absolute; left: -6px; right: -6px; top: 0; bottom: 0;
  border-top: 1.2px solid rgba(42,42,60,.72); border-bottom: 1.2px solid rgba(42,42,60,.72); pointer-events: none;
}}
body.style-architektur .value-badge::after, body.style-architektur .door-btn::after,
body.style-architektur .z-gebaeude-tab::after, body.style-architektur .z-floor-tab-l::after,
body.style-architektur .dimmer-val::after, body.style-architektur .nav-style-btn::after {{
  content: ''; position: absolute; top: -6px; bottom: -6px; left: 0; right: 0;
  border-left: 1.2px solid rgba(42,42,60,.72); border-right: 1.2px solid rgba(42,42,60,.72); pointer-events: none;
}}
body.style-architektur .value-badge {{ color: rgba(35,35,50,.92); }}
body.style-architektur .door-btn {{ color: rgba(35,35,50,.82); }}
body.style-architektur .z-gebaeude-tab {{ color: rgba(45,45,65,.7); }}
body.style-architektur .z-gebaeude-tab.active {{ color: rgba(25,25,40,.95); font-weight: 700; }}
body.style-architektur .z-floor-tab-l {{ color: rgba(45,45,65,.7); }}
body.style-architektur .z-floor-tab-l.active {{ color: rgba(25,25,40,.95); font-weight: 700; }}
body.style-architektur .nav-style-btn.active {{ font-weight: 700; color: rgba(25,25,40,.95); }}
body.style-architektur .nav-panel {{ background-color: #f0ede5; background-image: linear-gradient(rgba(80,95,155,0.14) 1px, transparent 1px), linear-gradient(90deg, rgba(80,95,155,0.14) 1px, transparent 1px), linear-gradient(rgba(120,135,180,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(120,135,180,0.05) 1px, transparent 1px); background-size: 40px 40px, 40px 40px, 4px 4px, 4px 4px; }}
body.style-architektur .nav-panel::before {{ display: none; }}
body.style-architektur .fp-room {{ stroke: rgba(42,42,60,.65); stroke-width: 1.5; }}
body.style-architektur .fp-txt {{ fill: rgba(35,35,50,.85); }}
body.style-architektur .fp-door {{ stroke: rgba(80,60,20,.6); }}
body.style-architektur .dot {{ border-color: rgba(42,42,60,.35); }}
body.style-architektur .dot.cur {{ background: rgba(42,42,60,.75); border-color: rgba(42,42,60,.75); }}
body.style-architektur .clip {{ background: linear-gradient(to bottom, #999, #777); }}
body.style-architektur input[type=range]::-webkit-slider-runnable-track {{ background: rgba(42,42,60,.4); }}
body.style-architektur input[type=range]::-webkit-slider-thumb {{ background: #f5f3ec; border-color: rgba(42,42,60,.7); }}

/* ── ENTERPRISE ── */
body.style-enterprise {{ background: #080810; background-image: radial-gradient(ellipse at 18% 28%, rgba(25,15,70,.55) 0%, transparent 52%), radial-gradient(ellipse at 82% 72%, rgba(70,15,15,.5) 0%, transparent 52%); }}
body.style-enterprise .zettel {{ background-color: #0e0e1c; background-image: none; border: 2px solid #252550; box-shadow: 0 0 50px rgba(25,25,180,.18), 0 0 8px rgba(0,0,0,.7), inset 0 0 50px rgba(0,0,12,.5); }}
body.style-enterprise .zettel::before {{ content: ''; position: absolute; left: 0; top: 0; right: 0; bottom: 0; width: auto; background: repeating-linear-gradient(0deg, transparent 0px, transparent 3px, rgba(0,0,0,.07) 3px, rgba(0,0,0,.07) 4px); pointer-events: none; z-index: 100; }}
body.style-enterprise .z-gebaeude-tabs {{ background: #0b0b18; border-bottom: 2px solid #b82000; gap: 2px; }}
body.style-enterprise .z-gebaeude-tab {{ background: #7a1800; color: #ffaa55; border: 1px solid #992000; border-radius: 1px; font-family: 'Courier New', monospace; font-size: .76rem; font-weight: bold; text-transform: uppercase; letter-spacing: .06em; filter: none; }}
body.style-enterprise .z-gebaeude-tab.active {{ background: #c03000; color: #ffe8bb; border-color: #ff4400; }}
body.style-enterprise .z-left-col {{ background: #080814; border-right: 1px solid #1a1a40; }}
body.style-enterprise .z-floor-tab-l {{ background: #001400; color: #00992e; border: 1px solid #003818; border-radius: 1px; font-family: 'Courier New', monospace; font-size: .75rem; font-weight: bold; text-transform: uppercase; filter: none; }}
body.style-enterprise .z-floor-tab-l.active {{ background: #004d1a; color: #00ff66; border-color: #00993a; text-shadow: 0 0 8px rgba(0,255,102,.6); }}
body.style-enterprise .nav-open-btn {{ color: #c8800a; }}
body.style-enterprise .z-floor {{ color: #3a5575; font-family: 'Courier New', monospace; font-size: .78rem; text-transform: uppercase; letter-spacing: .12em; }}
body.style-enterprise .z-title {{ color: #c8800a; font-family: 'Courier New', monospace; font-size: 1.5rem; font-weight: bold; border-bottom: 1px solid rgba(200,128,10,.35); text-transform: uppercase; letter-spacing: .04em; text-shadow: 0 0 12px rgba(200,128,10,.4); }}
body.style-enterprise .section-title {{ color: #3a5575; font-family: 'Courier New', monospace; font-size: .85rem; text-transform: uppercase; letter-spacing: .08em; text-decoration: none; }}
body.style-enterprise .ctrl-label {{ color: #c8800a; font-family: 'Courier New', monospace; }}
body.style-enterprise .ctrl-addr {{ color: #28374a; }}
body.style-enterprise hr.z-hr {{ border-top-color: rgba(35,50,80,.35); }}
body.style-enterprise .cb {{ clip-path: none; filter: none; background: #08081c; border: 2px solid #2a2a55; border-radius: 1px; }}
body.style-enterprise .ctrl.on .cb {{ background: #c8800a; border-color: #e09020; }}
body.style-enterprise .cb::after {{ color: #0e0e1c; font-size: 1.2rem; }}
body.style-enterprise .value-label {{ color: #c8800a; font-family: 'Courier New', monospace; }}
body.style-enterprise .value-badge {{ clip-path: none; filter: none; background: #001500; color: #00cc3d; border: 1px solid #004d1a; font-family: 'Courier New', monospace; font-weight: bold; font-size: 1.05rem; text-shadow: 0 0 8px rgba(0,200,60,.65); }}
body.style-enterprise .dimmer-label {{ color: #c8800a; font-family: 'Courier New', monospace; }}
body.style-enterprise .dimmer-val {{ filter: none; background: #001500; color: #00cc3d; border-color: #004d1a; font-family: 'Courier New', monospace; text-shadow: 0 0 6px rgba(0,200,60,.5); }}
body.style-enterprise input[type=range]::-webkit-slider-runnable-track {{ background: #2a2a55; }}
body.style-enterprise input[type=range]::-webkit-slider-thumb {{ background: #c8800a; border-color: #e09020; box-shadow: 0 0 6px rgba(200,128,10,.6); }}
body.style-enterprise .z-doors {{ border-top-color: rgba(50,55,100,.4); }}
body.style-enterprise .doors-label {{ color: #3a5575; font-family: 'Courier New', monospace; text-transform: uppercase; font-size: .78rem; }}
body.style-enterprise .door-btn {{ clip-path: none; filter: none; background: #160800; color: #ff9933; border: 1px solid #5a2800; border-radius: 1px; font-family: 'Courier New', monospace; text-transform: uppercase; font-size: .88rem; letter-spacing: .04em; text-shadow: 0 0 8px rgba(255,153,51,.45); }}
body.style-enterprise .door-btn:active {{ background: #bb4e00; color: #111; text-shadow: none; }}
body.style-enterprise .fp-room {{ stroke: #2a4466; fill: transparent; }}
body.style-enterprise .fp-room:hover, body.style-enterprise .fp-room:active {{ fill: rgba(200,128,10,.18); }}
body.style-enterprise .fp-txt {{ fill: #c8800a; font-family: 'Courier New', monospace; }}
body.style-enterprise .fp-door {{ stroke: #b82000; }}
body.style-enterprise .dot {{ border-color: #2a2a55; }}
body.style-enterprise .dot.cur {{ background: #c8800a; border-color: #c8800a; box-shadow: 0 0 6px rgba(200,128,10,.6); }}
body.style-enterprise .nav-btn {{ background: rgba(14,14,30,.9); border-color: #2a2a55; color: #c8800a; }}
body.style-enterprise .swipe-tip {{ color: rgba(58,85,117,.45); font-family: 'Courier New', monospace; font-size: .7rem; }}
body.style-enterprise .nav-overlay {{ background: rgba(0,0,0,.65); }}
body.style-enterprise .nav-panel {{ background-color: #080812; background-image: none; border-right: 3px solid #b82000; box-shadow: 4px 0 35px rgba(184,32,0,.2); }}
body.style-enterprise .nav-panel::before {{ display: none; }}
body.style-enterprise .nav-panel-head {{ border-bottom-color: rgba(35,35,80,.5); }}
body.style-enterprise .nav-panel-title {{ color: #c8800a; font-family: 'Courier New', monospace; text-transform: uppercase; letter-spacing: .05em; border-bottom-color: rgba(200,128,10,.35); text-shadow: 0 0 10px rgba(200,128,10,.35); }}
body.style-enterprise .nav-close-btn {{ color: #b82000; }}
body.style-enterprise .nav-gebaeude-label {{ color: #b82000; font-family: 'Courier New', monospace; text-transform: uppercase; font-size: .88rem; }}
body.style-enterprise .nav-floor-label {{ color: #2a3d55; font-family: 'Courier New', monospace; font-size: .72rem; }}
body.style-enterprise .nav-room-item {{ color: #c8800a; font-family: 'Courier New', monospace; font-size: .93rem; text-transform: uppercase; }}
body.style-enterprise .nav-room-item.cur {{ background: rgba(200,128,10,.14); font-weight: bold; }}
body.style-enterprise .nav-style-footer {{ border-top-color: rgba(35,35,80,.4); }}
body.style-enterprise .nav-style-title {{ color: #3a5575; font-family: 'Courier New', monospace; letter-spacing: .06em; }}
body.style-enterprise .nav-style-btn {{ background: #0b0b1e; border: 1px solid #2a2a55; color: #c8800a; font-family: 'Courier New', monospace; font-size: .88rem; text-transform: uppercase; filter: none; }}
body.style-enterprise .nav-style-btn:hover {{ background: #14143a; }}
body.style-enterprise .nav-style-btn.active {{ background: #14143a; border-color: #c8800a; color: #ffcc66; font-weight: bold; }}
body.style-enterprise .clip {{ background: linear-gradient(to bottom, #b82000, #7a1400); box-shadow: 0 0 12px rgba(184,32,0,.55); }}
body.style-enterprise .clip::after {{ background: rgba(255,80,30,.3); }}

/* ── DASHBOARD ── */
body.style-dashboard {{ background: #d8d4cc; background-image: none; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif; }}
body.style-dashboard .zettel {{ background-color: #fff; background-image: none; border: 1px solid #ccc9c3; border-radius: 4px; box-shadow: 0 1px 6px rgba(0,0,0,.10); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }}
body.style-dashboard .zettel::before {{ display: none; }}
body.style-dashboard .clip {{ background: linear-gradient(to bottom, #bbb, #999); box-shadow: 0 2px 5px rgba(0,0,0,.25); }}
body.style-dashboard .z-left-col {{ background: #e8e5e0; border-right: 1px solid #ccc9c3; }}
body.style-dashboard .z-floor-tab-l {{ background: #f3f1ee; color: #555; border: 1px solid #c0bdb8; border-radius: 3px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; font-size: .78rem; font-weight: 600; letter-spacing: .03em; filter: none; text-transform: uppercase; }}
body.style-dashboard .z-floor-tab-l.active {{ background: #1a4a6b; color: #fff; border-color: #1a4a6b; font-weight: 700; }}
body.style-dashboard .nav-open-btn {{ color: #555; filter: none; }}
body.style-dashboard .z-gebaeude-tabs {{ background: #e8e5e0; border-bottom: 2px solid #1a4a6b; }}
body.style-dashboard .z-gebaeude-tab {{ background: #f3f1ee; color: #444; border: 1px solid #c0bdb8; border-radius: 3px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; font-size: .76rem; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; filter: none; }}
body.style-dashboard .z-gebaeude-tab.active {{ background: #1a4a6b; color: #fff; border-color: #1a4a6b; }}
body.style-dashboard .z-floor {{ font-size: .72rem; font-weight: 700; letter-spacing: .10em; text-transform: uppercase; color: #888; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }}
body.style-dashboard .z-title {{ font-size: 1.25rem; font-weight: 700; color: #1a1a1a; border-bottom: none; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }}
body.style-dashboard .section-title {{ font-size: .72rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: #aaa; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; text-decoration: none; }}
body.style-dashboard hr.z-hr {{ border-top-color: #ebe8e3; }}
body.style-dashboard .ctrl-label {{ color: #2a2a2a; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }}
body.style-dashboard .ctrl-addr {{ color: #bbb; }}
body.style-dashboard .cb {{ clip-path: none; filter: none; background: #f3f3f1; border: 1.5px solid #bbb; border-radius: 3px; }}
body.style-dashboard .ctrl.on .cb {{ background: #1e6b1e; border-color: #1e6b1e; }}
body.style-dashboard .cb::after {{ color: #fff; font-size: 1.1rem; }}
body.style-dashboard .value-label {{ color: #555; font-family: inherit; }}
body.style-dashboard .value-badge {{ clip-path: none; filter: none; background: #f3f3f1; color: #c0392b; border: 1px solid #ddd; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; font-weight: 700; font-size: 1.1rem; }}
body.style-dashboard .dimmer-label {{ color: #555; font-family: inherit; }}
body.style-dashboard .dimmer-val {{ filter: none; background: #f3f3f1; color: #444; border-color: #ddd; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }}
body.style-dashboard input[type=range]::-webkit-slider-runnable-track {{ background: #ddd; }}
body.style-dashboard input[type=range]::-webkit-slider-thumb {{ background: #1a4a6b; border-color: #1a4a6b; box-shadow: none; }}
body.style-dashboard .z-doors {{ border-top-color: #ebe8e3; }}
body.style-dashboard .doors-label {{ color: #aaa; text-transform: uppercase; font-size: .72rem; letter-spacing: .08em; }}
body.style-dashboard .door-btn {{ clip-path: none; filter: none; background: #f3f3f1; color: #1a4a6b; border: 1px solid #bbb; border-radius: 3px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; font-size: .88rem; font-weight: 600; }}
body.style-dashboard .door-btn:active {{ background: #1a4a6b; color: #fff; border-color: #1a4a6b; }}
body.style-dashboard .fp-room {{ stroke: rgba(26,74,107,.65); stroke-width: 2; }}
body.style-dashboard .fp-room:hover, body.style-dashboard .fp-room:active {{ fill: rgba(26,74,107,.12); }}
body.style-dashboard .fp-txt {{ fill: #1a4a6b; }}
body.style-dashboard .fp-door {{ stroke: #8B6410; }}
body.style-dashboard .dot {{ border-color: #bbb; }}
body.style-dashboard .dot.cur {{ background: #1a4a6b; border-color: #1a4a6b; box-shadow: none; }}
body.style-dashboard .nav-btn {{ background: rgba(255,255,255,.85); border-color: #ccc; color: #444; }}
body.style-dashboard .swipe-tip {{ color: rgba(100,100,120,.35); font-size: .7rem; }}
body.style-dashboard .nav-overlay {{ background: rgba(0,0,0,.4); }}
body.style-dashboard .nav-panel {{ background-color: #e8e5e0; background-image: none; border-right: 3px solid #1a4a6b; box-shadow: 4px 0 20px rgba(0,0,0,.15); }}
body.style-dashboard .nav-panel::before {{ display: none; }}
body.style-dashboard .nav-panel-head {{ border-bottom-color: #ccc9c3; }}
body.style-dashboard .nav-panel-title {{ color: #1a4a6b; font-family: inherit; text-transform: uppercase; letter-spacing: .05em; border-bottom-color: #1a4a6b; }}
body.style-dashboard .nav-close-btn {{ color: #555; }}
body.style-dashboard .nav-gebaeude-label {{ color: #1a4a6b; font-family: inherit; font-size: .78rem; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; }}
body.style-dashboard .nav-floor-label {{ color: #999; font-family: inherit; font-size: .72rem; }}
body.style-dashboard .nav-room-item {{ color: #333; font-family: inherit; font-size: .93rem; }}
body.style-dashboard .nav-room-item.cur {{ background: rgba(26,74,107,.12); font-weight: 600; color: #1a4a6b; }}
body.style-dashboard .nav-style-footer {{ border-top-color: #ccc9c3; }}
body.style-dashboard .nav-style-title {{ color: #999; font-family: inherit; letter-spacing: .05em; }}
body.style-dashboard .nav-style-btn {{ background: #f3f1ee; border: 1px solid #c0bdb8; color: #444; font-family: inherit; font-size: .88rem; filter: none; }}
body.style-dashboard .nav-style-btn:hover {{ background: #e5e2de; }}
body.style-dashboard .nav-style-btn.active {{ background: #1a4a6b; border-color: #1a4a6b; color: #fff; font-weight: 600; }}
</style>
</head>
<body>

<svg style="position:absolute;width:0;height:0;overflow:hidden" aria-hidden="true">
  <defs>
    <filter id="sketchy" x="-8%" y="-8%" width="116%" height="116%">
      <feTurbulence type="fractalNoise" baseFrequency="0.055 0.065" numOctaves="3" seed="7" result="noise"/>
      <feDisplacementMap in="SourceGraphic" in2="noise" scale="2.2" xChannelSelector="R" yChannelSelector="G"/>
    </filter>
    <filter id="pencil" x="-10%" y="-10%" width="120%" height="120%">
      <feTurbulence type="fractalNoise" baseFrequency="0.042 0.036" numOctaves="4" seed="12" result="noise"/>
      <feDisplacementMap in="SourceGraphic" in2="noise" scale="1.3" xChannelSelector="R" yChannelSelector="G"/>
    </filter>
  </defs>
</svg>

<div id="nav-overlay" class="nav-overlay hidden" onclick="closeNav()">
  <div class="nav-panel" onclick="event.stopPropagation()">
    <div class="nav-panel-head">
      <span class="nav-panel-title">Alle Räume</span>
      <button class="nav-close-btn" onclick="closeNav()">✕</button>
    </div>
    <div id="nav-list" class="nav-list"></div>
    <div class="nav-style-footer">
      <div class="nav-style-title">Stil</div>
      <div class="nav-style-btns">
        <button class="nav-style-btn" data-style="zetterl"     onclick="setStyle('zetterl')">📝 Zetterl</button>
        <button class="nav-style-btn" data-style="architektur" onclick="setStyle('architektur')">📐 Architektur</button>
        <button class="nav-style-btn" data-style="enterprise"  onclick="setStyle('enterprise')">🖖 Enterprise</button>
        <button class="nav-style-btn" data-style="dashboard"   onclick="setStyle('dashboard')">📊 Dashboard</button>
      </div>
    </div>
  </div>
</div>

<div id="stage-container">
  <div class="stage-wrap" id="stage-wrap-a">
    <div class="stage" id="stage">
      <div class="clip"></div>
      <div class="zettel" id="zettel"></div>
      <button class="nav-btn btn-prev" onclick="historyNav('back')">◀</button>
      <button class="nav-btn btn-next" onclick="historyNav('fwd')">▶</button>
    </div>
    <div style="display:flex;align-items:center;gap:6px;"><div class="page-dots" id="dots"></div><div id="knx-dot" title="KNX getrennt" style="width:8px;height:8px;border-radius:50%;background:#e74c3c;flex-shrink:0;margin-bottom:2px;transition:background .4s;"></div></div>
  </div>
</div>

<script>
/* ── Konfiguration ── */
const GEBAEUDE    = {GEBAEUDE_JS};
const FLOOR_ORDER = {FLOOR_ORDER_JS};
const FLOOR_ABBR  = {FLOOR_ABBR_JS};
const FLOOR_LABEL = {FLOOR_LABEL_JS};

/* ── Devices ── */
const DEVICES = {{
{{DEVICES_PLACEHOLDER}}
}};

/* ── Rooms ── */
const ROOMS = [
{{ROOMS_PLACEHOLDER}}
];

/* ── STATE ── */
const STATE = {{}};
Object.entries(DEVICES).forEach(([id, dev]) => {{
  if (dev.type === 'switch')  STATE[id] = false;
  if (dev.type === 'dimmer')  STATE[id] = dev.value ?? 50;
  if (dev.type === 'display') STATE[id] = dev.value ?? '—';
}});

/* ── KNX WebSocket ── */
const KNX_WS_URL = `ws://${{location.hostname}}:8765`;
let knxWs = null;

function knxConnect() {{
  knxWs = new WebSocket(KNX_WS_URL);
  knxWs.onopen  = () => {{ updateKnxDot(true);  }};
  knxWs.onclose = () => {{ updateKnxDot(false); setTimeout(knxConnect, 3000); }};
  knxWs.onmessage = (e) => {{
    const d = JSON.parse(e.data);
    Object.entries(DEVICES).forEach(([devId, dev]) => {{
      if (dev.knx !== d.ga) return;
      if (dev.type === 'switch') {{
        STATE[devId] = Boolean(d.value);
        document.querySelectorAll(`.ctrl[data-devid="${{devId}}"]`).forEach(el => {{
          el.classList.toggle('on', STATE[devId]);
        }});
        playPaper(0.3);
      }} else if (dev.type === 'dimmer') {{
        STATE[devId] = +d.value;
        document.querySelectorAll(`input[data-devid="${{devId}}"]`).forEach(el => {{
          el.value = d.value;
          const row = el.closest('.dimmer-row');
          if (row) {{ const dv = row.querySelector('.dimmer-val'); if (dv) dv.textContent = d.value + '%'; }}
        }});
      }}
    }});
  }};
  knxWs.onerror = () => {{}};
}}

function knxSend(ga, value) {{
  if (ga && ga !== '—' && knxWs?.readyState === WebSocket.OPEN) {{
    knxWs.send(JSON.stringify({{ga, value}}));
  }}
}}

function updateKnxDot(ok) {{
  const dot = document.getElementById('knx-dot');
  if (dot) {{ dot.style.background = ok ? '#27ae60' : '#e74c3c'; dot.title = ok ? 'KNX verbunden' : 'KNX getrennt'; }}
}}

knxConnect();

function resolveCtrl(c) {{
  const dev = c.device ? DEVICES[c.device] : null;
  return {{
    label: c.alias || dev?.name || c.label || '?',
    type:  c.type  || dev?.type  || 'switch',
    knx:   c.knx   || dev?.knx   || '—',
    value: (c.device ? STATE[c.device] : c.value) ?? dev?.value ?? 50,
    unit:  c.unit  || dev?.unit  || '',
    devId: c.device || null,
  }};
}}

/* ── Audio ── */
let audioCtx = null;
function playPaper(vol=0.75){{
  try {{
    if (!audioCtx) audioCtx = new (window.AudioContext||window.webkitAudioContext)();
    const dur=.18,len=Math.floor(audioCtx.sampleRate*dur),buf=audioCtx.createBuffer(1,len,audioCtx.sampleRate),d=buf.getChannelData(0);
    for(let i=0;i<len;i++){{const t=i/len;d[i]=(Math.random()*2-1)*Math.sin(Math.PI*t)*Math.pow(1-t,1.5)*.45;}}
    const src=audioCtx.createBufferSource();src.buffer=buf;
    const hp=audioCtx.createBiquadFilter();hp.type='highpass';hp.frequency.value=2200;
    const g=audioCtx.createGain();g.gain.value=vol;
    src.connect(hp);hp.connect(g);g.connect(audioCtx.destination);src.start();
  }} catch(e) {{}}
}}

/* ── Hilfsfunktionen ── */
function roomById(id){{return ROOMS.find(r=>r.id===id);}}
function idxById(id){{return ROOMS.findIndex(r=>r.id===id);}}
function esc(s){{return String(s).replace(/"/g,'&quot;');}}
function getFloorsForBuilding(geb){{const s=new Set();ROOMS.filter(r=>r.gebaeude===geb).forEach(r=>s.add(r.floor));return FLOOR_ORDER.filter(f=>s.has(f));}}

function renderLeftCol(room) {{
  const floors = getFloorsForBuilding(room.gebaeude);
  const tabs = floors.length > 1
    ? floors.map(f => {{
        const abbr = FLOOR_ABBR[f] || f.substring(0,3);
        return `<div class="z-floor-tab-l ${{room.floor===f?'active':''}}" onclick="navToFloor('${{esc(room.gebaeude)}}','${{esc(f)}}')">${{abbr}}</div>`;
      }}).join('')
    : '';
  return `<div class="z-left-col">
    <button class="nav-open-btn" onclick="openNav()">☰</button>
    ${{tabs}}
  </div>`;
}}

function renderGebaeudeTabs(room) {{
  return `<div class="z-gebaeude-tabs">` +
    GEBAEUDE.map(g => `<button class="z-gebaeude-tab ${{room.gebaeude===g.id?'active':''}}" onclick="navToGebaeude('${{g.id}}')">${{g.short}}</button>`).join('') +
  `</div>`;
}}

function render(idx, targetEl, isB) {{
  const room = ROOMS[idx];
  const el = targetEl || document.getElementById('zettel');
  if (!el) return;
  if (room.type === 'floorplan') {{ el.innerHTML = renderFloorplan(room); attachFloorplanEvents(el); }}
  else if (room.type === 'floorlist') {{ el.innerHTML = renderFloorList(room); }}
  else {{ el.innerHTML = renderRoom(room); attachRoomEvents(el, room); }}
  if (!targetEl) renderDots();
}}

function renderRoom(room) {{
  const controls = (room.controls||[]).map(resolveCtrl);
  const sw  = controls.filter(c=>c.type==='switch');
  const dim = controls.filter(c=>c.type==='dimmer');
  const dis = controls.filter(c=>c.type==='display');
  let html = '';
  if (sw.length) {{
    html += `<div class="section-title">Schalten</div>`;
    sw.forEach(c => {{
      const on = c.devId ? STATE[c.devId] : false;
      html += `<div class="ctrl ${{on?'on':''}}" data-devid="${{esc(c.devId||'')}}">
        <div class="cb"></div>
        <span class="ctrl-label">${{c.label}}</span>
        <span class="ctrl-addr" title="${{DEVICES[c.devId]?.name||c.label}}">${{c.knx}}</span>
      </div>`;
    }});
  }}
  if (dim.length) {{
    html += `<hr class="z-hr"><div class="section-title">Dimmen / Fahren</div>`;
    dim.forEach(c => {{
      const val = c.devId ? STATE[c.devId] : c.value;
      html += `<div class="dimmer-row">
        <div class="dimmer-label">
          <span>${{c.label}} <span class="ctrl-addr">${{c.knx}}</span></span>
          <span class="dimmer-val" data-devid="${{esc(c.devId||'')}}">${{val}}%</span>
        </div>
        <input type="range" min="0" max="100" value="${{val}}" data-devid="${{esc(c.devId||'')}}">
      </div>`;
    }});
  }}
  if (dis.length) {{
    html += `<hr class="z-hr"><div class="section-title">Messwerte</div>`;
    dis.forEach(c => {{
      const val = c.devId ? STATE[c.devId] : c.value;
      html += `<div class="value-row">
        <span class="value-label">${{c.label}} <span class="ctrl-addr">${{c.knx}}</span></span>
        <span class="value-badge">${{val}} ${{c.unit}}</span>
      </div>`;
    }});
  }}
  if (!sw.length && !dim.length && !dis.length) {{
    html = `<div style="padding:20px 0;color:var(--ink-light);font-size:1.1rem">Keine steuerbaren Geräte erfasst.</div>`;
  }}
  /* Sonderinhalt je Raum */
  if (room.id === 'AUSSEN_Schröten') {{
    html += `<div class="yt-wrap" style="position:relative;cursor:pointer;"
        onclick="window.open('https://www.youtube.com/watch?v=3yMIk9u--lo','_blank')">
      <img src="https://img.youtube.com/vi/3yMIk9u--lo/hqdefault.jpg"
        style="width:100%;height:100%;object-fit:cover;display:block;" alt="Schildkröten">
      <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;">
        <div style="width:56px;height:56px;border-radius:50%;background:rgba(255,0,0,.85);
          display:flex;align-items:center;justify-content:center;">
          <span style="color:#fff;font-size:22px;margin-left:4px;">▶</span>
        </div>
      </div>
    </div>`;
  }}
  return `${{renderLeftCol(room)}}${{renderGebaeudeTabs(room)}}
    <div class="z-head">
      <div class="z-floor">${{room.floor||''}} · ${{GEBAEUDE.find(g=>g.id===room.gebaeude)?.name||''}}</div>
      <div class="z-title">${{room.name}}</div>
    </div>
    <div class="z-body">${{html}}</div>
    ${{buildDoors(room.doors)}}
    <div class="swipe-tip">← wischen →</div>`;
}}

function attachRoomEvents(el, room) {{
  el.querySelectorAll('.ctrl[data-devid]').forEach(ctrl => {{
    ctrl.addEventListener('click', ev => {{
      if (ev.target.tagName==='INPUT'||ev.target.classList.contains('ctrl-addr')) return;
      const devId=ctrl.dataset.devid;
      if (!devId||DEVICES[devId]?.type!=='switch') return;
      STATE[devId]=!STATE[devId]; ctrl.classList.toggle('on',STATE[devId]);
      knxSend(DEVICES[devId]?.knx, STATE[devId]);
    }});
  }});
  el.querySelectorAll('input[type=range]').forEach(inp => {{
    inp.addEventListener('input', () => {{
      const devId=inp.dataset.devid; if(!devId)return;
      STATE[devId]=+inp.value;
      inp.closest('.dimmer-row').querySelector('[data-devid]').textContent=inp.value+'%';
      knxSend(DEVICES[devId]?.knx, +inp.value);
    }});
  }});
  attachDoorEvents(el);
}}

/* ── Grundriss ── */
function renderFloorplan(room) {{
  const floors = {{"KG":"Keller","EG":"Erdgeschoss","OG":"Obergeschoss","DG":"Dachgeschoss","AUSSEN":"Außenbereich","UG":"UG"}};
  const floorRows = Object.entries(floors).map(([f,label]) => {{
    const roomsOnFloor = ROOMS.filter(r => r.floor===f && r.type==='room' && r.gebaeude==='haupthaus');
    if (!roomsOnFloor.length) return '';
    const items = roomsOnFloor.slice(0,8).map((r,i) => {{
      const x = 15 + (i%4)*78, y = 18 + Math.floor(i/4)*55;
      return `<rect class="fp-room" data-target="${{r.id}}" x="${{x}}" y="${{y}}" width="72" height="48"/>
              <text class="fp-txt" x="${{x+36}}" y="${{y+24}}" style="font-size:10px">${{r.name}}</text>`;
    }}).join('');
    return items;
  }});

  /* Vereinfachter Grundriss EG */
  const svg = `<svg class="fp-svg" viewBox="0 0 340 480" xmlns="http://www.w3.org/2000/svg">
    <rect x="5" y="5" width="330" height="470" fill="none" stroke="var(--ink)" stroke-width="2"/>
    <text x="170" y="22" class="fp-txt" style="font-size:14px;font-weight:700">CPP Hortigstrasse – EG</text>
    <rect class="fp-room" data-target="EG_Eingang"     x="8"   y="30"  width="100" height="70"/><text class="fp-txt" x="58"  y="65">Eingang</text>
    <rect class="fp-room" data-target="EG_Windfang"    x="108" y="30"  width="80"  height="70"/><text class="fp-txt" x="148" y="65">Windfang</text>
    <rect class="fp-room" data-target="EG_Gang"        x="188" y="30"  width="145" height="70"/><text class="fp-txt" x="260" y="65">Gang / Flur</text>
    <rect class="fp-room" data-target="EG_Wohnzimmer"  x="8"   y="105" width="180" height="130"/><text class="fp-txt" x="98"  y="170">Wohnzimmer</text>
    <rect class="fp-room" data-target="EG_Küche"       x="188" y="105" width="145" height="90"/><text class="fp-txt" x="260" y="150">Küche</text>
    <rect class="fp-room" data-target="EG_Büro"        x="188" y="195" width="145" height="75"/><text class="fp-txt" x="260" y="232">Büro</text>
    <rect class="fp-room" data-target="EG_Wellness"    x="8"   y="235" width="180" height="110"/><text class="fp-txt" x="98"  y="290">Wellness</text>
    <rect class="fp-room" data-target="EG_Treppenhaus" x="8"   y="345" width="120" height="70"/><text class="fp-txt" x="68"  y="380">Treppenhaus</text>
    <rect class="fp-room" data-target="EG_Terrasse-EG" x="128" y="345" width="205" height="70"/><text class="fp-txt" x="230" y="380">Terrasse EG</text>
    <rect class="fp-room" data-target="EG_WC"          x="188" y="270" width="70"  height="70"/><text class="fp-txt" x="223" y="305">WC</text>
    <rect class="fp-room" data-target="EG_Bad-WC"      x="258" y="270" width="75"  height="70"/><text class="fp-txt" x="295" y="305">Bad-WC</text>
    <rect class="fp-room" data-target="EG_Technik"     x="8"   y="415" width="120" height="55"/><text class="fp-txt" x="68"  y="442">Technik</text>
    <rect class="fp-room" data-target="EG_Sauna"       x="128" y="415" width="100" height="55"/><text class="fp-txt" x="178" y="442">Sauna</text>
    <path class="fp-door" d="M108,65 Q118,65 118,80"/><path class="fp-door" d="M188,100 Q188,92 200,92"/>
    <text x="308" y="22" style="font-family:var(--font);font-size:13px;fill:#999">N↑</text>
  </svg>`;
  return `${{renderLeftCol(room)}}${{renderGebaeudeTabs(room)}}
    <div class="z-head"><div class="z-floor">Übersicht · CPP Villa</div><div class="z-title">CPP Hortigstrasse</div></div>
    <div class="floorplan-wrap">${{svg}}</div>
    <div class="swipe-tip">Raum antippen oder → wischen</div>`;
}}
function attachFloorplanEvents(el){{el.querySelectorAll('.fp-room').forEach(r=>{{r.addEventListener('click',()=>{{const ti=idxById(r.dataset.target);if(ti>=0)navigateTo(ti);}});}});}}

/* ── Zimmerliste (Geschoss-Übersicht) ── */
function renderFloorList(room) {{
  const floorRooms = ROOMS.filter(r => r.floor===room.floor && r.type==='room' && r.gebaeude===room.gebaeude);
  const grid = floorRooms.map(r => {{
    const devCount = (r.controls||[]).length;
    return `<button class="floorlist-btn" onclick="navigateInstant(idxById('${{r.id}}'))">
      <span class="floorlist-name">${{r.name}}</span>
      <span class="floorlist-count">${{devCount}} Gerät${{devCount===1?'':'e'}}</span>
    </button>`;
  }}).join('');
  return `${{renderLeftCol(room)}}${{renderGebaeudeTabs(room)}}
    <div class="z-head">
      <div class="z-floor">${{FLOOR_LABEL[room.floor]||room.floor}}</div>
      <div class="z-title">${{room.name}}</div>
    </div>
    <div class="z-body">
      <div class="floorlist-grid">${{grid||'<span style="color:var(--ink-light)">Keine Räume.</span>'}}</div>
    </div>
    <div class="swipe-tip">Raum antippen oder ← → wischen</div>`;
}}

/* ── Türen ── */
function buildDoors(doors){{
  if(!doors?.length)return'';
  const items=doors.map(id=>{{const r=roomById(id);if(!r)return'';return`<button class="door-btn" data-target="${{id}}">🚪 ${{r.name}}</button>`;}}).join('');
  if(!items.trim())return'';
  return`<div class="z-doors"><div class="doors-label">Verbindungen:</div><div class="doors-row">${{items}}</div></div>`;
}}
function attachDoorEvents(el){{
  el.querySelectorAll('.door-btn').forEach(btn=>{{btn.addEventListener('click',()=>{{const ti=idxById(btn.dataset.target);if(ti>=0)navigateTo(ti);}});}});
}}

/* ── Dots ── */
function renderDots(){{const c=document.getElementById('dots');if(!c)return;c.innerHTML=ROOMS.map((_,i)=>`<div class="dot ${{i===currentIdx?'cur':''}}" onclick="navigateTo(${{i}})"></div>`).join('');}}

/* ── Navigation ── */
let currentIdx=0,isAnimating=false;
function navigateTo(idx){{if(isAnimating||idx===currentIdx)return;const dir=idx>currentIdx?'left':'right';history.pushState({{idx,from:currentIdx}},'','#'+ROOMS[idx].id);animateTo(idx,dir);}}
function historyNav(dir){{if(dir==='back')history.back();else history.forward();}}
function navToGebaeude(id){{const r=ROOMS.find(r=>r.gebaeude===id);if(r)navigateInstant(idxById(r.id));}}
function navToFloor(geb,floor){{const r=ROOMS.find(r=>r.gebaeude===geb&&r.floor===floor);if(r)navigateInstant(idxById(r.id));}}
window.addEventListener('popstate',e=>{{
  const newIdx=(e.state?.idx!=null)?e.state.idx:idxById(location.hash.replace('#',''));
  if(newIdx==null||newIdx<0||newIdx===currentIdx)return;
  animateTo(newIdx,newIdx>currentIdx?'left':'right');
}});
function animateTo(toIdx,dir){{
  if(isAnimating)return;isAnimating=true;playPaper();
  const stageEl=document.getElementById('stage'),oldEl=document.getElementById('zettel');
  oldEl.style.transition='transform .32s cubic-bezier(.4,0,.2,1), opacity .32s';
  oldEl.style.transform=dir==='left'?'translateX(-105%) rotateY(-6deg)':'translateX(105%) rotateY(6deg)';oldEl.style.opacity='0';
  const newEl=document.createElement('div');newEl.className='zettel';newEl.style.transition='none';
  newEl.style.transform=dir==='left'?'translateX(105%) rotateY(6deg)':'translateX(-105%) rotateY(-6deg)';newEl.style.opacity='0';
  stageEl.appendChild(newEl);currentIdx=toIdx;render(currentIdx,newEl);renderDots();
  requestAnimationFrame(()=>requestAnimationFrame(()=>{{
    newEl.style.transition='transform .32s cubic-bezier(.4,0,.2,1), opacity .32s';newEl.style.transform='translateX(0) rotateY(0deg)';newEl.style.opacity='1';
    setTimeout(()=>{{oldEl.remove();newEl.id='zettel';isAnimating=false;}},360);
  }}));
}}
function navigateInstant(idx){{
  if(isAnimating||idx===currentIdx)return;currentIdx=idx;
  history.replaceState({{idx,from:idx}},'','#'+ROOMS[idx].id);
  const el=document.getElementById('zettel');if(!el)return;
  el.style.opacity='0';
  requestAnimationFrame(()=>{{render(currentIdx,el);renderDots();el.style.transition='opacity .15s';el.style.opacity='1';setTimeout(()=>el.style.transition='',200);}});
}}

/* ── Stil ── */
function setStyle(name){{
  document.body.classList.remove('style-zetterl','style-architektur','style-enterprise','style-dashboard');
  document.body.classList.add('style-'+name);
  try{{localStorage.setItem('cpp_style',name);}}catch(e){{}}
  document.querySelectorAll('.nav-style-btn').forEach(b=>b.classList.toggle('active',b.dataset.style===name));
}}

/* ── Swipe ── */
let touchStartX=0,touchStartY=0,isDragging=false,mouseDown=false;
const stageEl=document.getElementById('stage');
stageEl.addEventListener('touchstart',e=>{{if(e.target.tagName==='INPUT'||e.target.closest('.z-left-col,.page-dots'))return;touchStartX=e.touches[0].clientX;touchStartY=e.touches[0].clientY;isDragging=false;}},{{passive:true}});
stageEl.addEventListener('touchmove',e=>{{if(e.target.tagName==='INPUT')return;const dx=Math.abs(e.touches[0].clientX-touchStartX),dy=Math.abs(e.touches[0].clientY-touchStartY);if(dx>8&&dx>dy)isDragging=true;}},{{passive:true}});
stageEl.addEventListener('touchend',e=>{{if(!isDragging)return;isDragging=false;const dx=e.changedTouches[0].clientX-touchStartX;if(Math.abs(dx)>45){{if(dx>0)history.back();else history.forward();}}}});
stageEl.addEventListener('mousedown',e=>{{if(e.target.tagName==='INPUT'||e.target.closest('.z-left-col,.page-dots'))return;mouseDown=true;touchStartX=e.clientX;isDragging=false;}});
stageEl.addEventListener('mousemove',e=>{{if(!mouseDown)return;if(Math.abs(e.clientX-touchStartX)>8)isDragging=true;}});
stageEl.addEventListener('mouseup',e=>{{if(!mouseDown)return;mouseDown=false;if(!isDragging)return;isDragging=false;const dx=e.clientX-touchStartX;if(Math.abs(dx)>45){{if(dx>0)history.back();else history.forward();}}}});
document.addEventListener('keydown',e=>{{if(e.key==='ArrowLeft')history.back();if(e.key==='ArrowRight')history.forward();if(e.key==='Escape')closeNav();}});
/* ── Nav-Overlay ── */
function buildNavList(){{
  const icons={{floorplan:'🏠',floorlist:'📋',room:'·'}};
  return GEBAEUDE.map(g=>{{
    const rooms=ROOMS.filter(r=>r.gebaeude===g.id);if(!rooms.length)return'';
    return`<div class="nav-gebaeude-label">📍 ${{g.name}}</div>`+
      getFloorsForBuilding(g.id).map(floor=>`<div class="nav-floor-label">${{floor}}</div>`+
        rooms.filter(r=>r.floor===floor).map(r=>`<div class="nav-room-item ${{r.id===ROOMS[currentIdx]?.id?'cur':''}}" onclick="navTo('${{r.id}}')"><span style="opacity:.55">${{icons[r.type]||'·'}}</span>${{r.name}}</div>`).join('')
      ).join('');
  }}).join('');
}}
function openNav(){{document.getElementById('nav-list').innerHTML=buildNavList();document.getElementById('nav-overlay').classList.remove('hidden');}}
function closeNav(){{document.getElementById('nav-overlay').classList.add('hidden');}}
function navTo(id){{closeNav();const ti=idxById(id);if(ti>=0)navigateTo(ti);}}

/* ── Init ── */
const params=new URLSearchParams(location.search);
const dp=params.get('display');if(dp)document.body.classList.add('mode-'+dp);
(function(){{try{{const s=localStorage.getItem('cpp_style')||'zetterl';document.body.classList.add('style-'+s);document.querySelectorAll('.nav-style-btn').forEach(b=>b.classList.toggle('active',b.dataset.style===s));}}catch(e){{document.body.classList.add('style-zetterl');}}}})();
const hashId=location.hash.replace('#',''),hashIdx=idxById(hashId);
if(hashIdx>=0)currentIdx=hashIdx;
render(currentIdx);
history.replaceState({{idx:currentIdx,from:-1}},'','#'+ROOMS[currentIdx].id);
renderDots();
</script>
</body>
</html>"""

# Ersetze Platzhalterhtml = html.replace("{GEBAEUDE_JS}", GEBAEUDE_JS)
html = html.replace("{FLOOR_ORDER_JS}", FLOOR_ORDER_JS)
html = html.replace("{FLOOR_ABBR_JS}", FLOOR_ABBR_JS)
html = html.replace("{FLOOR_LABEL_JS}", FLOOR_LABEL_JS)
html = html.replace("{DEVICES_PLACEHOLDER}", render_devices())
html = html.replace("{ROOMS_PLACEHOLDER}", render_rooms())

with open(HTML_OUT, "w", encoding="utf-8") as f:
    f.write(html)

size_kb = os.path.getsize(HTML_OUT) // 1024
print(f"\n✅ Fertig: {HTML_OUT}")
print(f"   Größe: {size_kb} kB")
print(f"   Räume: {len(rooms_out)}")
print(f"   Devices: {len(devices_js)}")
