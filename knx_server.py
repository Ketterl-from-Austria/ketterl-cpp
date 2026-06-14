"""
knx_server.py – KNX WebSocket-Server + HTTP-Dashboard

Verbindet sich mit dem KNX IP-Interface, empfaengt alle Telegramme und
leitet sie per WebSocket an das Dashboard weiter. Sendet Steuerbefehle
vom Dashboard an den KNX-Bus.

Installation:
    pip install xknx websockets

Aufruf:
    # Erst Dashboard generieren:
    python ets_parser.py mein_export.csv          -> knx_config.json
    python dashboard_gen.py knx_config.json       -> dashboard.html

    # Dann Server starten:
    python knx_server.py --config knx_config.json --knx 192.168.1.100

    # Oder direkt aus ETS-Export:
    python knx_server.py --ets export.csv --knx 192.168.1.100

    # Demo-Modus (kein KNX-Interface noetig):
    python knx_server.py --config knx_config.json --demo

    Browser: http://localhost:8080
"""

from __future__ import annotations
import asyncio, json, logging, sys, argparse, threading
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from functools import partial

try:
    import websockets
    import websockets
except ImportError:
    print("FEHLER: pip install websockets", file=sys.stderr); sys.exit(1)

try:
    from xknx import XKNX
    from xknx.io import ConnectionConfig, ConnectionType
    from xknx.telegram import Telegram, GroupAddress, TelegramDirection
    from xknx.dpt import DPTBinary, DPTArray
    HAS_XKNX = True
except ImportError:
    HAS_XKNX = False
    print("INFO: xknx nicht installiert – KNX-Verbindung deaktiviert.", file=sys.stderr)
    print("INFO: pip install xknx", file=sys.stderr)

log = logging.getLogger("knx_server")
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S")

# ─── Zustand ──────────────────────────────────────────────────────────────────
knx_state: dict[str, object] = {}   # GA -> letzter Wert
clients: set = set()                 # WebSocket-Verbindungen
xknx_inst = None                     # XKNX-Instanz

# ─── WebSocket Broadcast ──────────────────────────────────────────────────────
async def broadcast(msg: dict):
    if not clients:
        return
    data = json.dumps(msg, ensure_ascii=False)
    dead = set()
    for c in list(clients):
        try:
            await c.send(data)
        except Exception:
            dead.add(c)
    clients -= dead

# ─── KNX Empfang ─────────────────────────────────────────────────────────────
async def on_telegram(telegram: "Telegram"):
    ga  = str(telegram.destination_address)
    val = decode_telegram(telegram)
    if val is None:
        return
    knx_state[ga] = val
    log.info(f"KNX  {ga:12s}  = {val}")
    await broadcast({"type": "status", "ga": ga, "value": val})

def decode_telegram(telegram):
    """Dekodiert Telegramm-Payload zu Python-Wert."""
    payload = telegram.payload
    if payload is None:
        return None
    try:
        raw = payload.value
        if isinstance(raw, (bool, int, float)):
            return raw
        if isinstance(raw, (list, tuple)) and raw:
            if len(raw) == 1:
                return raw[0]
            # 2-Byte Float (Temperatur etc.)
            if len(raw) == 2:
                try:
                    from xknx.dpt import DPTTemperature
                    return DPTTemperature.from_knx(raw)
                except Exception:
                    return int.from_bytes(raw, "big")
        return str(raw)
    except Exception:
        return None

# ─── KNX Senden ──────────────────────────────────────────────────────────────
async def send_to_knx(ga: str, value, dpt: str):
    if not HAS_XKNX or xknx_inst is None:
        log.info(f"[Demo] -> {ga} = {value}")
        knx_state[ga] = value
        await broadcast({"type": "status", "ga": ga, "value": value})
        return
    try:
        from xknx.dpt import DPTBinary, DPTArray
        from xknx.telegram import Telegram, GroupAddress, TelegramDirection
        from xknx.core.value_reader import ValueReader
        # Payload je nach DPT bestimmen
        dpt_up = (dpt or "").upper()
        if "18" in dpt_up:  # Szene
            payload = DPTArray((0x80 | int(value)) if isinstance(value, int) else 0x80)
        elif "9." in dpt_up or "FLOAT2" in dpt_up or "TEMP" in dpt_up.upper():
            from xknx.dpt import DPTTemperature
            raw = DPTTemperature.to_knx(float(value))
            payload = DPTArray(raw)
        elif "5." in dpt_up or "PERCENT" in dpt_up or "VALUE8" in dpt_up:
            pct = max(0, min(255, int(float(value) * 255 / 100)))
            payload = DPTArray((pct,))
        else:
            # 1-Bit Binaer
            payload = DPTBinary(1 if (value is True or str(value) in ("1","true","True")) else 0)

        telegram = Telegram(
            destination_address=GroupAddress(ga),
            direction=TelegramDirection.OUTGOING,
            payload=payload,
        )
        await xknx_inst.telegrams.put(telegram)
        knx_state[ga] = value
        await broadcast({"type": "status", "ga": ga, "value": value})
        log.info(f"Sende {ga:12s}  = {value}")
    except Exception as ex:
        log.error(f"KNX-Sendefehler {ga}: {ex}")

# ─── WebSocket Handler ────────────────────────────────────────────────────────
async def ws_handler(websocket):
    clients.add(websocket)
    remote = websocket.remote_address
    log.info(f"Client verbunden: {remote}")
    # Aktuellen Zustand senden
    if knx_state:
        await websocket.send(json.dumps({"type": "init", "states": knx_state}))
    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "cmd":
                await send_to_knx(msg["ga"], msg["value"], msg.get("dpt",""))
            elif msg.get("type") == "init_req":
                await websocket.send(json.dumps({"type": "init", "states": knx_state}))
    except Exception as ex:
        log.debug(f"Client {remote}: {ex}")
    finally:
        clients.discard(websocket)
        log.info(f"Client getrennt: {remote}")

# ─── HTTP Server (Dashboard) ──────────────────────────────────────────────────
class DashHandler(SimpleHTTPRequestHandler):
    def __init__(self, *a, dashboard_path=None, **kw):
        self._dashboard = dashboard_path
        super().__init__(*a, directory=str(dashboard_path.parent), **kw)

    def do_GET(self):
        if self.path in ("/", "/index.html", "/dashboard.html"):
            self.path = "/" + self._dashboard.name
        super().do_GET()

    def log_message(self, fmt, *args):
        pass  # HTTP-Log unterdruecken

def start_http(dashboard_path: Path, port: int):
    handler = partial(DashHandler, dashboard_path=dashboard_path)
    httpd   = HTTPServer(("", port), handler)
    log.info(f"HTTP  -> http://localhost:{port}")
    httpd.serve_forever()

# ─── KNX Verbindung ───────────────────────────────────────────────────────────
async def start_knx(config: dict, knx_ip: str, mode: str = "TUNNELING"):
    global xknx_inst
    if not HAS_XKNX:
        return None
    conn_type = ConnectionType.ROUTING if mode == "ROUTING" else ConnectionType.TUNNELING
    conn_conf = ConnectionConfig(connection_type=conn_type, gateway_ip=knx_ip)
    x = XKNX(connection_config=conn_conf)
    # Telegram-Callback registrieren
    x.telegram_queue.register_telegram_received_cb(on_telegram)
    await x.start()
    xknx_inst = x
    log.info(f"KNX   verbunden mit {knx_ip} ({mode})")
    # Alle konfigurierten GAs einlesen
    log.info("KNX   lese aktuellen Zustand ...")
    return x

# ─── Demo Modus ──────────────────────────────────────────────────────────────
async def demo_ticker(config: dict):
    """Sendet alle 10s Zufallswerte zum Testen."""
    import random
    while True:
        await asyncio.sleep(10)
        for room in config.get("rooms", []):
            for dev in room.get("devices", {}).values():
                for addr in dev.get("addresses", []):
                    ga  = addr["ga"]
                    cat = addr["dpt_cat"]
                    if not ga: continue
                    if cat == "binary":
                        val = random.random() > 0.5
                    elif cat == "percent":
                        val = random.randint(0,100)
                    elif cat == "temperature":
                        val = round(18 + random.random() * 6, 1)
                    else:
                        continue
                    knx_state[ga] = val
                    await broadcast({"type":"status","ga":ga,"value":val})

# ─── Haupt ────────────────────────────────────────────────────────────────────
async def amain(args):
    # Config laden
    if args.ets:
        from ets_parser import build_config
        config = build_config(Path(args.ets))
        cfg_path = Path(args.ets).with_name("knx_config.json")
        cfg_path.write_text(json.dumps(config, indent=2, ensure_ascii=False))
        log.info(f"Geparst: {config['meta']['total_gas']} GAs, {config['meta']['total_rooms']} Raeume")
    else:
        cfg_path = Path(args.config)
        config = json.loads(cfg_path.read_text(encoding="utf-8"))

    # Dashboard generieren falls noetig
    if args.zettel:
        dash_path = cfg_path.with_name("dashboard_zettel.html")
        if not dash_path.exists() or args.ets:
            from dashboard_zettel import generate_html
            dash_path.write_text(
                generate_html(config, ws_port=args.ws_port), encoding="utf-8")
            log.info(f"Zettel-Dashboard: {dash_path}")
    else:
        dash_path = cfg_path.with_name("dashboard.html")
        if not dash_path.exists() or args.ets:
            from dashboard_gen import generate_html
            dash_path.write_text(
                generate_html(config, ws_port=args.ws_port), encoding="utf-8")
            log.info(f"Dashboard: {dash_path}")

    # HTTP starten (Thread)
    http_thread = threading.Thread(
        target=start_http, args=(dash_path, args.http_port), daemon=True)
    http_thread.start()

    # KNX verbinden (wenn nicht Demo)
    knx = None
    if not args.demo and args.knx:
        try:
            knx = await start_knx(config, args.knx, args.knx_mode)
        except Exception as ex:
            log.warning(f"KNX-Verbindung fehlgeschlagen: {ex} – starte Demo-Modus")
            args.demo = True
    elif not args.knx:
        log.info("KNX   kein IP angegeben – Demo-Modus")
        args.demo = True

    if args.demo:
        asyncio.create_task(demo_ticker(config))
        log.info("KNX   Demo-Modus aktiv (Zufallswerte alle 10s)")

    # WebSocket starten
    log.info(f"WS    -> ws://localhost:{args.ws_port}/ws")
    async with websockets.serve(ws_handler, "0.0.0.0", args.ws_port, ping_interval=20):
        log.info(f"Dashboard: http://localhost:{args.http_port}")
        log.info("Ctrl+C zum Beenden")
        if knx:
            await knx.join()
        else:
            await asyncio.Future()  # laeuft ewig

def main():
    p = argparse.ArgumentParser(description="KNX WebSocket-Server + Dashboard")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--ets",    help="ETS CSV-Export (wird automatisch geparst)")
    src.add_argument("--config", help="knx_config.json", default="knx_config.json")
    p.add_argument("--knx",       help="KNX IP-Interface IP-Adresse (z.B. 192.168.1.100)")
    p.add_argument("--knx-mode",  choices=["TUNNELING","ROUTING"], default="TUNNELING")
    p.add_argument("--http-port", type=int, default=8080)
    p.add_argument("--ws-port",   type=int, default=8765)
    p.add_argument("--demo",      action="store_true", help="Demo-Modus ohne echtes KNX")
    p.add_argument("--zettel",    action="store_true", help="Zettel-Stil Dashboard (dashboard_zettel.html)")
    args = p.parse_args()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        log.info("Beendet.")

if __name__ == "__main__":
    main()
