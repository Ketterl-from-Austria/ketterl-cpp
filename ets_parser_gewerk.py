#!/usr/bin/env python3
"""
ets_parser_gewerk.py  –  ETS CSV Parser für Gewerk-sortierte Exporte

CSV-Format:  Tab-delimited, 9 Spalten (0-8), kein Header, Encoding cp1252
Spalten:     0=Gewerk  1=Funktion  2=GA-Name  3=Adresse  4=Zentral  7=DPT

Ausgabe:     knx_config.json  (identische Struktur wie ets_parser.py)

Aufruf:
    python ets_parser_gewerk.py GA-Export.csv
    python ets_parser_gewerk.py GA-Export.csv -o knx_config.json --project "CPP Hortigstrasse"
    python ets_parser_gewerk.py GA-Export.csv --preview
"""

from __future__ import annotations
import csv, json, re, sys, argparse
from pathlib import Path
from collections import defaultdict

# ─── DPT-Klassifizierung  (aus ets_parser.py übernommen) ─────────────────────
DPT_MAP = {
    "DPT-1":  "binary",       "DPT 1": "binary",      "1 Bit":  "binary",
    "DPST-1-1":  "binary",    "DPST-1-2":  "binary",  "DPST-1-3": "binary",
    "DPST-1-5":  "binary",    "DPST-1-7":  "binary",  "DPST-1-8": "blind_move",
    "DPST-1-9":  "blind_stop","DPST-1-11": "binary",  "DPST-1-17": "binary",
    "DPST-1-19": "binary",    "DPST-1-24": "binary",
    "DPT-3":  "dimmer_ctrl",  "DPT 3":   "dimmer_ctrl",
    "DPST-3-7":  "dimmer_ctrl",
    "DPT-5":  "value8",       "DPT 5":   "value8",    "8 Bit":  "value8",
    "DPST-5-1":  "percent",   "DPST-5-2": "angle",
    "DPT-9":  "float2",       "DPT 9":   "float2",
    "DPST-9-1":  "temperature","DPST-9-2":"temperature","DPST-9-4":"lux",
    "DPST-9-5":  "speed",     "DPST-9-7":"humidity",  "DPST-9-25":"flow",
    "DPT-14": "float4",       "DPT-20":  "hvac_mode",
    "DPST-20-102":"hvac_mode","DPT-16":  "string",    "DPT-17":"scene_number",
    "DPT-18": "scene_ctrl",   "DPT 18":  "scene_ctrl",
    "DPST-19-1": "datetime",
}

def classify_dpt(raw: str) -> str:
    if not raw: return "unknown"
    r = raw.strip().upper()
    for key, val in DPT_MAP.items():
        if key.upper() in r:
            return val
    m = re.match(r"DPT[-\s]?(\d+)", r)
    if m:
        n = int(m.group(1))
        if n == 1:  return "binary"
        if n == 3:  return "dimmer_ctrl"
        if n == 5:  return "value8"
        if n == 9:  return "float2"
        if n == 18: return "scene_ctrl"
    return "unknown"

# ─── Funktions- und Subfunktions-Erkennung ────────────────────────────────────
FUNC_KEYWORDS = {
    "light":    ["licht","lampe","leuchte","beleuchtung","led","spot","strahler",
                 "light","lamp","dimmer","dimm","ambiente","lux","nachtlicht",
                 "bodenheizung außen","bodenheizung au"],
    "blind":    ["jalousie","jal","rollo","rolladen","raffstore","lamelle","markise",
                 "beschattung","store","shutter","blind","fenster bewegen",
                 "fenster auf","fenster schritt"],
    "heating":  ["heizung","fussboden","fbh","heizkörper","radiator",
                 "ist-temperatur","soll-temperatur","betriebsart","sollwert"],
    "socket":   ["steckdose","dose","socket","outlet"],
    "temperature":["temperatur","temp","messwert"],
}

SUBFUNC_KEYWORDS = {
    "switch":   ["schalten","switch"," ein/"," aus/","ein/aus"],
    "dim":      ["dimmen","dim","helligkeit","brightness"],
    "move":     [" fahren","bewegen","auf/ab","auf/zu","bewegen","start"],
    "stop":     ["schritt/stop","start/stop","stop","halt","stopp"],
    "position": ["% fahren","pos ","position"],
    "feedback": ["rückmeldung"," rm"," rm "," wert","status","istwert","fbk","feedback"],
    "actual":   ["ist-temperatur","ist-temp"],
    "setpoint": ["soll-temperatur","sollwert","soll ","soll-"],
    "angle":    ["lamelle","winkel","angle","neigung","lamellenstellung"],
    "scene":    ["szene","scene"],
}

def detect_function(name: str, dpt_cat: str) -> tuple[str, str]:
    """Gibt (func, subfunc) zurück – gleiche Logik wie ets_parser.py."""
    name_l = name.lower()

    # subfunc
    subfunc = "unknown"
    for sf, kws in SUBFUNC_KEYWORDS.items():
        if any(k in name_l for k in kws):
            subfunc = sf
            break
    if dpt_cat == "blind_move":    subfunc = "move"
    elif dpt_cat == "blind_stop":  subfunc = "stop"
    elif dpt_cat == "scene_ctrl":  subfunc = "scene"
    elif dpt_cat == "temperature":
        subfunc = "setpoint" if any(k in name_l for k in ["soll","sollwert"]) else "actual"
    elif dpt_cat in ("percent","value8") and "pos" in name_l:
        subfunc = "position"

    # func
    func = "other"
    for f, kws in FUNC_KEYWORDS.items():
        if any(k in name_l for k in kws):
            func = f
            break
    if func == "other":
        if dpt_cat in ("binary","dimmer_ctrl"):        func = "light"
        elif dpt_cat == "temperature":                 func = "heating"
        elif dpt_cat in ("scene_ctrl","scene_number"): func = "scene"
    # Jalousie override: move/stop DPT → blind
    if dpt_cat in ("blind_move","blind_stop"):
        func = "blind"
    return func, subfunc

# ─── Raumerkennung ─────────────────────────────────────────────────────────────
# (prefix_lowercase,  stockwerk,  raumname)   – längste zuerst → greedy match
# fmt: off
_ROOM_PREFIXES_RAW: list[tuple[str, str, str]] = [
    # ── KG / UG ──────────────────────────────────────────────────────────────
    ("fritzl keller",               "KG",     "Fritzl-Keller"),
    ("fritzl keller",               "KG",     "Fritzl-Keller"),
    # ── EG – spezifisch vor allgemein ─────────────────────────────────────────
    ("eg wozi süd",                 "EG",     "Wohnzimmer"),
    ("eg wozi",                     "EG",     "Wohnzimmer"),
    ("wohnen heizung",              "EG",     "Wohnzimmer"),
    ("wohnen ",                     "EG",     "Wohnzimmer"),
    ("eg windfang",                 "EG",     "Windfang"),
    ("eg wellness",                 "EG",     "Wellness"),
    ("eg sauna",                    "EG",     "Wellness"),
    ("eg dusche",                   "EG",     "Wellness"),
    ("eg kanal e",                  "EG",     "Wellness"),
    ("eg w garten",                 "EG",     "Wellness"),     # EG W Garten Stellgröße
    ("eg küche",                    "EG",     "Küche"),
    ("küche ",                      "EG",     "Küche"),
    ("küche",                       "EG",     "Küche"),
    ("eg treppenhaus",              "EG",     "Treppenhaus"),
    ("treppenhaus stufen",          "EG",     "Treppenhaus"),
    ("treppenhaus wand",            "EG",     "Treppenhaus"),
    ("treppenhaus",                 "EG",     "Treppenhaus"),
    ("handlauf",                    "EG",     "Treppenhaus"),
    ("gäste wc",                    "EG",     "Gäste-WC"),
    ("bad/wc",                      "EG",     "Bad-WC"),
    ("abstellraum/keller",          "EG",     "Abstellraum"),
    ("hauswirtschaftsraum",         "EG",     "Hauswirtschaft"),
    ("eg wc",                       "EG",     "WC"),
    ("eg büro",                     "EG",     "Büro"),
    ("eg gang",                     "EG",     "Gang"),
    ("eg eingangsbereich",          "EG",     "Eingang"),
    ("eg eingang",                  "EG",     "Eingang"),
    ("eg vitrine",                  "EG",     "Eingang"),
    ("eg türöffner",                "EG",     "Eingang"),
    ("eg technik",                  "EG",     "Technik"),
    ("eg funksteckdose",            "EG",     "Technik"),
    ("eg terrasse",                 "EG",     "Terrasse-EG"),
    ("eg markise",                  "EG",     "Terrasse-EG"),
    ("eg carport",                  "EG",     "Carport"),
    ("eg garten wc",                "AUSSEN", "Garten-WC"),
    ("eg licht außen",              "AUSSEN", "Garten"),
    ("eg fassade",                  "AUSSEN", "Garten"),
    ("eg außen",                    "AUSSEN", "Garten"),
    ("licht außen",                 "AUSSEN", "Garten"),
    ("außenlicht von",              "AUSSEN", "Garten"),
    ("nachtlicht außen",            "AUSSEN", "Garten"),
    # ── OG – KiZi ────────────────────────────────────────────────────────────
    ("og kizi 01",                  "OG",     "Kinderzimmer-01"),
    ("og kizi 02",                  "OG",     "Kinderzimmer-02"),
    ("og kizi 03",                  "OG",     "Kinderzimmer-03"),
    ("og kizi 04",                  "OG",     "Kinderzimmer-04"),
    ("og kizi01",                   "OG",     "Kinderzimmer-01"),  # ohne Leerzeichen
    ("og kizi02",                   "OG",     "Kinderzimmer-02"),
    ("og kizi03",                   "OG",     "Kinderzimmer-03"),
    ("og kizi04",                   "OG",     "Kinderzimmer-04"),
    ("eg kizi02",                   "OG",     "Kinderzimmer-02"),  # ETS-Typo
    ("eg kizi01",                   "OG",     "Kinderzimmer-01"),
    ("kizi 01 jal",                 "OG",     "Kinderzimmer-01"),
    ("kizi 01 jak",                 "OG",     "Kinderzimmer-01"),
    ("kizi 01",                     "OG",     "Kinderzimmer-01"),
    ("kizi01",                      "OG",     "Kinderzimmer-01"),
    ("kizi 02 jal",                 "OG",     "Kinderzimmer-02"),
    ("kizi 02",                     "OG",     "Kinderzimmer-02"),
    ("kizi02",                      "OG",     "Kinderzimmer-02"),
    ("kizi 03 jalousie",            "OG",     "Kinderzimmer-03"),
    ("kizi 03 jal",                 "OG",     "Kinderzimmer-03"),
    ("kizi 03",                     "OG",     "Kinderzimmer-03"),
    ("kizi 04 jal",                 "OG",     "Kinderzimmer-04"),
    ("kizi 04",                     "OG",     "Kinderzimmer-04"),
    ("markise og kizi03",           "OG",     "Kinderzimmer-03"),
    # ── OG – andere ──────────────────────────────────────────────────────────
    ("og ankleide",                 "OG",     "Ankleide"),
    ("og bad",                      "OG",     "Bad-OG"),
    ("og wc",                       "OG",     "WC-OG"),
    ("og hwr",                      "OG",     "HWR"),
    ("hwr jal",                     "OG",     "HWR"),
    ("og chillout-zone",            "OG",     "Chillout-Zone"),
    ("chillout-zone",               "OG",     "Chillout-Zone"),
    ("og garage",                   "OG",     "Garage"),
    ("og einfahrt garage",          "OG",     "Garage"),
    ("og einfahrt",                 "OG",     "Garage"),
    ("garagentor",                  "OG",     "Garage"),
    ("og garderobe",                "OG",     "Garderobe"),
    ("og treppenhaus",              "OG",     "Treppenhaus-OG"),
    ("og terrasse",                 "OG",     "Terrasse-OG"),
    ("og gang",                     "OG",     "Gang-OG"),
    ("og vorraum",                  "OG",     "Vorraum"),
    ("og carport",                  "OG",     "Carport-OG"),
    ("og vorgartenbeleuchtung",     "AUSSEN", "Vorgarten"),
    ("bad jal",                     "OG",     "Bad-OG"),
    # ── DG ───────────────────────────────────────────────────────────────────
    ("dg schlafzimmer",             "DG",     "Schlafzimmer"),
    ("dg schlazi",                  "DG",     "Schlafzimmer"),
    ("og schlazi",                  "DG",     "Schlafzimmer"),  # ETS-Typo: OG statt DG
    ("markise dg schlazi",          "DG",     "Schlafzimmer"),
    ("dg licht",                    "DG",     "Schlafzimmer"),  # "DG Licht Kasten Ambiente"
    ("dg ankleide",                 "DG",     "Ankleide-DG"),
    ("dg bad",                      "DG",     "Bad-DG"),
    ("dg jal. velux",               "DG",     "Bad-DG"),
    ("dg fenster",                  "DG",     "Bad-DG"),        # ABUS Fensterkontakt
    ("dg wc",                       "DG",     "WC-DG"),
    ("dg gästezimmer",              "DG",     "Gästezimmer"),
    ("dg gang",                     "DG",     "Gang-DG"),
    ("dg dachboden",                "DG",     "Dachboden"),
    ("dg terrasse",                 "DG",     "Terrasse-DG"),
    ("dg technikraum",              "DG",     "Technikraum-DG"),
    ("dg garage",                   "DG",     "Heizung-DG"),    # "DG Garage Sollwert +/-"
    ("dg serverr.",                 "DG",     "Serverraum-DG"), # "DG Serverr. 1/2 Ventil"
    ("dg außenlicht",               "DG",     "Terrasse-DG"),
    ("dg außenstrahler",            "DG",     "Terrasse-DG"),
    # ── AUSSEN ───────────────────────────────────────────────────────────────
    ("schröten",                    "AUSSEN", "Schröten"),
    ("aussen schacht beim gartenpool","AUSSEN","Gartenpool"),
    ("gartenpool schacht",          "AUSSEN", "Gartenpool"),
    ("gartenpool",                  "AUSSEN", "Gartenpool"),
    ("pool wasserfall",             "AUSSEN", "Gartenpool"),
    ("pool  hauptpumpe",            "AUSSEN", "Gartenpool"),
    ("pool hauptpumpe",             "AUSSEN", "Gartenpool"),
    ("12fach aktor",                "AUSSEN", "Pool-Technik"),
    ("schacht bei sportpool",       "AUSSEN", "Sportpool"),
    ("sportpool pumpe",             "AUSSEN", "Sportpool"),
    ("pumpe sportpool",             "AUSSEN", "Sportpool"),
    ("markise sportpool licht",     "AUSSEN", "Sportpool"),
    ("markise sportpool",           "AUSSEN", "Sportpool"),
    ("sportpool licht",             "AUSSEN", "Sportpool"),
    ("sportpool",                   "AUSSEN", "Sportpool"),
    ("sportplatz licht",            "AUSSEN", "Sportplatz"),
    ("sportplatz",                  "AUSSEN", "Sportplatz"),
    ("garteneingang licht",         "AUSSEN", "Garteneingang"),
    ("garteneingang",               "AUSSEN", "Garteneingang"),
    ("gartenwand ost",              "AUSSEN", "Gartenwand"),
    ("gartenwand strahler",         "AUSSEN", "Gartenwand"),
    ("gartenwand steckdose",        "AUSSEN", "Gartenwand"),
    ("gartenwand",                  "AUSSEN", "Gartenwand"),
    ("garten wc",                   "AUSSEN", "Garten-WC"),
    ("garten  pool terrasse",       "AUSSEN", "Pool-Terrasse"),
    ("garten pool terrasse",        "AUSSEN", "Pool-Terrasse"),
    ("garten markise essplatz",     "AUSSEN", "Pool-Terrasse"),
    ("markise essplatz garten",     "AUSSEN", "Pool-Terrasse"),
    ("markise griller licht",       "AUSSEN", "Grill-Platz"),
    ("markise griller",             "AUSSEN", "Grill-Platz"),
    ("bäume süden",                 "AUSSEN", "Garten"),
    ("bodenheizung außen",          "AUSSEN", "Bodenheizung-außen"),
    ("bodenheizung au",             "AUSSEN", "Bodenheizung-außen"),
    ("bewässerung",                 "AUSSEN", "Bewässerung"),
    ("zirkulationspumpe",           "AUSSEN", "Technik-Außen"),
    ("ist-temp. gartenpool",        "AUSSEN", "Gartenpool"),
    ("ist-temp. sportpool",         "AUSSEN", "Sportpool"),
    ("markise eg wozi süd",         "EG",     "Wohnzimmer"),
    ("markise eg",                  "EG",     "Terrasse-EG"),
    # ── Heizung / Test-Zeug ──────────────────────────────────────────────────
    ("ha kg",                       "KG",     "Heizung-KG"),
    ("ha eg 01",                    "EG",     "Heizung-EG"),
    ("ha eg 02",                    "EG",     "Heizung-Wellness"),
    ("ha eg",                       "EG",     "Heizung-EG"),
    ("ha og",                       "OG",     "Heizung-OG"),
    ("ha dg 01",                    "DG",     "Heizung-Schlafzimmer"),
    ("ha dg 02",                    "DG",     "Serverraum-1"),
    ("ha dg 03",                    "DG",     "Serverraum-2"),
    ("ha dg",                       "DG",     "Heizung-DG"),
    ("schlazi ",                    "DG",     "Schlafzimmer"),   # Heizungsventile Schlazi
    ("keller ventilzustand",        "KG",     "Technik-KG"),
    ("ug fritzl-keller",            "UG",     "Fritzl-Keller"),  # UG Fritzl-Keller IST-Temp
    ("kg fritzl-keller",            "KG",     "Fritzl-Keller"),
    ("kg lichte",                   "KG",     "Keller"),          # Typo: "KG Lichte Decke"
    ("kg licht",                    "KG",     "Keller"),
    # ── Gewerke / Technik ────────────────────────────────────────────────────
    ("module status",               "EG",     "PV-Anlage"),
    ("firmware version",            "EG",     "PV-Anlage"),
    ("ac total",                    "EG",     "PV-Anlage"),
    ("ac current",                  "EG",     "PV-Anlage"),
    ("ac voltage",                  "EG",     "PV-Anlage"),
    ("ac frequency",                "EG",     "PV-Anlage"),
    ("ac apparent",                 "EG",     "PV-Anlage"),
    ("ac reactive",                 "EG",     "PV-Anlage"),
    ("ac daily",                    "EG",     "PV-Anlage"),
    ("dc total",                    "EG",     "PV-Anlage"),
    ("ac power",                    "EG",     "PV-Anlage"),
    ("dc power",                    "EG",     "PV-Anlage"),
    ("pv1 ",                        "EG",     "PV-Anlage"),
    ("pv2 ",                        "EG",     "PV-Anlage"),
    ("battery 1",                   "EG",     "PV-Anlage"),
    ("total charge",                "EG",     "PV-Anlage"),
    ("total discharge",             "EG",     "PV-Anlage"),
    ("powermeter",                  "EG",     "PV-Anlage"),
    ("verbrauch = 0",               "EG",     "PV-Anlage"),
    ("inverter",                    "EG",     "PV-Anlage"),
    ("incerter",                    "EG",     "PV-Anlage"),
    ("cabinet temperature",         "EG",     "PV-Anlage"),
    ("signalgeber",                 "EG",     "Technik"),
    ("gira eg eingang",             "EG",     "Technik-Gira"),
    ("gira kg",                     "KG",     "Technik-Gira"),
    ("intruder alert",              "EG",     "Technik"),
    ("näherung 30",                 "EG",     "Technik"),
    ("kem-",                        "AUSSEN", "Pool-Messung"),
    ("messung kem",                 "AUSSEN", "Pool-Messung"),
    ("messungkem",                  "AUSSEN", "Pool-Messung"),  # Typo (kein Leerzeichen)
    ("verbrauch kem",               "AUSSEN", "Pool-Messung"),
    ("durchfluss kem",              "AUSSEN", "Pool-Messung"),
    ("durchluss kem",               "AUSSEN", "Pool-Messung"),
    ("bab ",                        "EG",     "Wetter-BAB"),
    ("bab",                         "EG",     "Wetter-BAB"),   # bare "BAB" GA
    ("wetterinfo",                  "EG",     "Wetter-BAB"),
    # ── OG – Ventilzustand (Lüftung) ─────────────────────────────────────────
    ("og kizi01 ventilzustand",     "OG",     "Kinderzimmer-01"),
    ("og kizi02 ventilzustand",     "OG",     "Kinderzimmer-02"),
    ("og kizi03 ventilzustand",     "OG",     "Kinderzimmer-03"),
    ("og kizi04 ventilzustand",     "OG",     "Kinderzimmer-04"),
    # ── Diverses / Zentral ───────────────────────────────────────────────────
    ("schröten wasser",             "AUSSEN", "Schröten"),
    ("schröten sand",               "AUSSEN", "Schröten"),
    ("schröten luft",               "AUSSEN", "Schröten"),
    ("schröten",                    "AUSSEN", "Schröten"),
    ("pumpenanforderung",           "_ZE",    "Heizung-Zentral"),
    ("pumpenansforderung",          "_ZE",    "Heizung-Zentral"),  # Typo in ETS
    ("pumpe für ha",                "_ZE",    "Heizung-Zentral"),
    ("zentrale betriebsart",        "_ZE",    "Heizung-Zentral"),
    ("temperatur außen",            "AUSSEN", "Wetter"),
    ("geschwindigkeit",             "AUSSEN", "Wetter"),
    ("windrichtung",                "AUSSEN", "Wetter"),
    ("windalarm",                   "_ZE",    "Zentral-Alarms"),
    ("regenalarm",                  "_ZE",    "Zentral-Alarms"),
    ("regenssensor",                "_ZE",    "Zentral-Alarms"),
    ("tag/nacht",                   "_ZE",    "Zentral-Status"),
    ("datum und uhrzeit",           "_ZE",    "Zentral-Status"),
    ("datum einstellen",            "_ZE",    "Zentral-Status"),
    ("aktuelle zeit",               "_ZE",    "Zentral-Status"),
    ("dämmerung",                   "_ZE",    "Zentral-Status"),
    ("licht gesamt",                "_ZE",    "Zentral-Licht"),
    ("zentral bewegen",             "_ZE",    "Zentral-Jalousie"),
    ("zentral schritt",             "_ZE",    "Zentral-Jalousie"),
    ("wohnen gesamt",               "_ZE",    "Zentral-Wohnen"),
    ("küche gesamt",                "_ZE",    "Zentral-Küche"),
    ("stopp/lamellen",              "_ZE",    "Zentral-Jalousie"),
    ("status aktuelle position",    "_ZE",    "Zentral-Jalousie"),
    ("absolute position",           "_ZE",    "Zentral-Jalousie"),
    ("einschalts",                  "_ZE",    "Zentral-Status"),
    # ── Test-Zeug bare GAs ───────────────────────────────────────────────────
    ("schalten",                    "_ZE",    "Zentral-Test"),
    ("status",                      "_ZE",    "Zentral-Test"),
    ("bewegen",                     "_ZE",    "Zentral-Test"),
    ("schritt/stop",                "_ZE",    "Zentral-Test"),
    ("position",                    "_ZE",    "Zentral-Test"),
    ("lamellenstellung",            "_ZE",    "Zentral-Test"),
    ("test-text",                   "_ZE",    "Zentral-Test"),
    # ── Generischer Fallback für bekannte Stockwerke ──────────────────────────
    ("eg ",                         "EG",     "Sonstiges-EG"),
    ("og ",                         "OG",     "Sonstiges-OG"),
    ("dg ",                         "DG",     "Sonstiges-DG"),
    ("kg ",                         "KG",     "Sonstiges-KG"),
    ("ug ",                         "UG",     "Sonstiges-UG"),
]
# fmt: on

# Nach Präfix-Länge sortieren (greedy – längstes zuerst)
ROOM_PREFIXES = sorted(_ROOM_PREFIXES_RAW, key=lambda x: -len(x[0]))


def extract_room(ga_name: str) -> tuple[str, str, int]:
    """Gibt (Stockwerk, Raumname, Präfix-Länge-im-Original) zurück."""
    name_l = ga_name.lower()
    for prefix, floor, room in ROOM_PREFIXES:
        if name_l.startswith(prefix):
            return floor, room, len(prefix)
    return "EG", "Sonstiges", 0


# ─── Funktionssuffix-Bereinigung ──────────────────────────────────────────────
# Typische Suffixe am Ende von GA-Namen, die den Geräte-Basisnamen nicht ändern
_SUFFIX_RE = re.compile(
    r"\s+"
    r"(SCHALTEN|DIMMEN|WERT|RM|RÜCKMELDUNG|FAHREN|BEWEGEN"
    r"|SCHRITT[/.]?STOP|START[/.]?STOP|STOPP?|OFFEN|GESCHLOSSEN"
    r"|%\s*FAHREN|IST-TEMPERATUR|SOLL-TEMPERATUR|IST-TEMP"
    r"|STELLGR[ÖO](?:SS)?E?|BETRIEBSART\s+SCHALTEN|BETRIEBSART\s+STATUS"
    r"|SOLLWERT\s+[+/\-]+(?:\s+STATUS)?|MESSWERT|EIN/AUS|AUF/ZU"
    r"|STATUS|AUF/AB|1\s*ALARM|BETRIEB|ALARM)$",
    re.IGNORECASE,
)


def strip_suffix(name: str) -> str:
    m = _SUFFIX_RE.search(name)
    return name[: m.start()].strip() if m else name.strip()


# ─── CSV-Lesen ────────────────────────────────────────────────────────────────
def read_csv(filepath: Path) -> list[dict]:
    """
    Liest einen Gewerk-sortierten ETS CSV-Export.

    Spalten-Layout:
        0 = Gewerk (Hauptgruppe)   – nur in Gewerk-Headerzeilen befüllt
        1 = Funktion (Mittelgruppe) – nur in Funktions-Headerzeilen befüllt
        2 = GA-Name                 – nur in echten GA-Zeilen befüllt
        3 = Adresse (x/y/z oder x/-/- oder x/y/-)
        4 = Zentral-Flag ('true' oder leer)
        7 = DPT-Typ (DPST-X-Y)
    """
    text = None
    for enc in ("cp1252", "latin-1", "utf-8-sig"):
        try:
            text = filepath.read_text(encoding=enc, errors="replace")
            break
        except Exception:
            continue
    if text is None:
        raise IOError(f"Datei konnte nicht gelesen werden: {filepath}")

    gewerk = ""
    rows: list[dict] = []

    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parts = list(csv.reader([line], delimiter="\t"))[0]
        except Exception:
            continue
        # Sicherstellen, dass genug Spalten vorhanden
        parts = [p.strip('"') for p in parts]
        while len(parts) < 9:
            parts.append("")

        col0 = parts[0].strip()
        col1 = parts[1].strip()
        col2 = parts[2].strip()
        col3 = parts[3].strip()
        col4 = parts[4].strip()
        col7 = parts[7].strip()

        # Gewerk-Header (col0 befüllt, Adresse hat /- Format)
        if col0 and not re.match(r"^\d+/\d+/\d+$", col3):
            gewerk = col0
            continue

        # Echte GA: Adresse ist x/y/z
        if re.match(r"^\d+/\d+/\d+$", col3) and col2:
            rows.append({
                "gewerk":  gewerk,
                "name":    col2,
                "address": col3,
                "zentral": col4.lower() == "true",
                "dpt_raw": col7,
            })

    return rows


# ─── Config aufbauen ──────────────────────────────────────────────────────────
def _clean_label(name: str, prefix_len: int) -> str:
    """
    Erzeugt ein lesbares Label aus dem GA-Namen:
    Raum-Präfix + bekannte Zimmer-Abkürzungen entfernen.
    """
    # Raum-Präfix abschneiden
    label = name[prefix_len:].strip(" -–_")

    # Stockwerk-Kürzel am Anfang entfernen, falls noch vorhanden
    label = re.sub(
        r"^(EG|OG|DG|KG|UG|AUSSEN)\s+",
        "",
        label,
        flags=re.IGNORECASE,
    )
    # Bekannte Zimmer-Abkürzungen entfernen
    label = re.sub(
        r"^(WoZi|SchlaZi|KiZi\s*\d*|HWR|CIA)\s+",
        "",
        label,
        flags=re.IGNORECASE,
    )
    return label.strip() or name.strip()


def _device_key(func: str, base_name: str) -> str:
    """Erstellt einen stabilen Dict-Key für ein Gerät."""
    return re.sub(r"\s+", "_", f"{func}_{base_name}").lower()[:60]


def build_config(filepath: Path, project_name: str = "") -> dict:
    raw_rows = read_csv(filepath)

    if not project_name:
        project_name = (
            filepath.stem.replace("_", " ").replace("-", " ").title()
        )

    rooms: dict[str, dict] = {}      # key → room-dict
    func_counts: dict[str, int] = defaultdict(int)

    for ga in raw_rows:
        # Raum + Stockwerk aus GA-Namen ableiten
        floor, room, pfx_len = extract_room(ga["name"])

        # Zentral-GAs in separaten Pseudo-Raum (wird im Dashboard meist nicht gezeigt)
        if ga["zentral"]:
            floor = "_ZE"
            room  = "Zentral"
            pfx_len = 0

        room_key = f"{floor}_{room}"
        if room_key not in rooms:
            rooms[room_key] = {
                "id":      room_key,
                "name":    room,
                "floor":   floor,
                "devices": {},
            }

        # DPT + Funktion
        dpt_cat       = classify_dpt(ga["dpt_raw"])
        func, subfunc = detect_function(ga["name"], dpt_cat)

        # Geräte-Basisname: Suffix abschneiden, dann Label ableiten
        base_name = strip_suffix(ga["name"])
        label     = _clean_label(base_name, pfx_len)

        # Gerät anlegen oder wiederverwenden
        dev_key = _device_key(func, base_name)
        dev_id  = re.sub(r"[^\w]", "_", dev_key)

        if dev_id not in rooms[room_key]["devices"]:
            rooms[room_key]["devices"][dev_id] = {
                "id":          dev_id,
                "func":        func,
                "name_prefix": base_name,
                "label":       label,
                "addresses":   [],
            }

        rooms[room_key]["devices"][dev_id]["addresses"].append({
            "ga":       ga["address"],
            "name":     ga["name"],
            "dpt_raw":  ga["dpt_raw"],
            "dpt_cat":  dpt_cat,
            "subfunc":  subfunc,
            "comment":  "",
        })

        func_counts[func] += 1

    # Räume sortieren: Stockwerk-Reihenfolge
    floor_order = {"KG": 0, "EG": 1, "OG": 2, "DG": 3, "AUSSEN": 4, "_ZE": 99}
    room_list = sorted(
        rooms.values(),
        key=lambda r: (floor_order.get(r["floor"], 10), r["name"]),
    )

    # Nur sichtbare Stockwerke (ohne _ZE) ins Meta
    floors = sorted(
        set(r["floor"] for r in room_list if not r["floor"].startswith("_")),
        key=lambda f: floor_order.get(f, 10),
    )

    return {
        "meta": {
            "source_file":  filepath.name,
            "total_gas":    len(raw_rows),
            "total_rooms":  len(room_list),
            "total_scenes": 0,
            "floors":       floors,
            "func_summary": dict(func_counts),
        },
        "project": {
            "name":    project_name,
            "address": "",
            "contact": "",
        },
        "rooms":  room_list,
        "scenes": [],
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main() -> None:
    p = argparse.ArgumentParser(
        description="ETS Gewerk-CSV \u2192 knx_config.json"
    )
    p.add_argument("input",              help="ETS CSV (Gewerk-Struktur)")
    p.add_argument("-o", "--output",     help="Ausgabe JSON  [default: knx_config.json]")
    p.add_argument("--project",          help="Projektname", default="")
    p.add_argument("--preview", action="store_true",
                   help="Nur Vorschau \u2013 nichts schreiben")
    args = p.parse_args()

    in_path  = Path(args.input)
    out_path = (
        Path(args.output)
        if args.output
        else in_path.parent / "knx_config.json"
    )

    if not in_path.exists():
        print(f"\u274c  Datei nicht gefunden: {in_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Lese   {in_path.name}  (Gewerk-Modus) ...")
    config = build_config(in_path, args.project)
    meta   = config["meta"]

    print(f"\nGeparst:")
    print(f"   {meta['total_gas']:>4}  Gruppenadressen")
    print(f"   {meta['total_rooms']:>4}  Raeume  ({', '.join(meta['floors'])})")
    print(f"\n   Raeume:")
    for room in config["rooms"]:
        if room["floor"].startswith("_"):
            continue
        n_dev = len(room["devices"])
        n_ga  = sum(len(d["addresses"]) for d in room["devices"].values())
        print(f"   {room['floor']:6}  {room['name']:<28}  {n_dev:>3} Geraete  {n_ga:>4} GAs")
    ze = [r for r in config["rooms"] if r["floor"].startswith("_")]
    if ze:
        n = sum(len(r["devices"]) for r in ze)
        print(f"   Zentral  (Zentral/System-GAs)               {n:>3} Geraete")

    print(f"\n   Funktionen:")
    for func, cnt in sorted(meta["func_summary"].items(), key=lambda x: -x[1]):
        print(f"   {cnt:>4}x  {func}")

    if args.preview:
        return

    out_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nGespeichert: {out_path}")


if __name__ == "__main__":
    main()
