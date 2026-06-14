#!/usr/bin/env python3
"""
knx_bridge.py – KNX WebSocket Bridge für Ketterl CPP
Browser → WebSocket(:8765) → KNX/IP-Interface → KNX-Bus

Start: python3 knx_bridge.py
"""
import asyncio, json, logging
import websockets
from xknx import XKNX
from xknx.io import ConnectionConfig, ConnectionType
from xknx.telegram import Telegram, GroupAddress
from xknx.telegram.apci import GroupValueWrite
from xknx.dpt import DPTBinary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

# ── Konfiguration ────────────────────────────────────────────────────────────
KNX_IP   = "192.168.107.175"
KNX_PORT = 3671
WS_PORT  = 8765
# ─────────────────────────────────────────────────────────────────────────────

clients: set = set()
xknx_inst: XKNX = None


async def knx_start():
    global xknx_inst
    xknx_inst = XKNX(connection_config=ConnectionConfig(
        connection_type=ConnectionType.TUNNELING,
        gateway_ip=KNX_IP,
        gateway_port=KNX_PORT,
    ))
    await xknx_inst.start()
    log.info(f"KNX verbunden: {KNX_IP}:{KNX_PORT}")


async def knx_write(ga: str, value: bool):
    t = Telegram(
        destination_address=GroupAddress(ga),
        payload=GroupValueWrite(DPTBinary(1 if value else 0)),
    )
    await xknx_inst.telegrams.put(t)
    log.info(f"KNX write  {ga} = {'EIN' if value else 'AUS'}")


async def broadcast(msg: str, skip=None):
    for c in list(clients):
        if c is skip:
            continue
        try:
            await c.send(msg)
        except Exception:
            clients.discard(c)


async def ws_handler(ws):
    clients.add(ws)
    log.info(f"Client verbunden: {ws.remote_address}  (gesamt: {len(clients)})")
    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
                ga    = data["ga"]
                value = bool(data["value"])
                await knx_write(ga, value)
                # State an alle Clients broadcasten (auch Sender)
                await broadcast(json.dumps({"ga": ga, "value": value}))
            except Exception as e:
                log.error(f"Fehler: {e}")
                await ws.send(json.dumps({"error": str(e)}))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        clients.discard(ws)
        log.info(f"Client getrennt  (gesamt: {len(clients)})")


async def main():
    await knx_start()
    async with websockets.serve(ws_handler, "0.0.0.0", WS_PORT):
        log.info(f"WebSocket läuft auf :{WS_PORT}")
        log.info(f"Testseite: http://ketterl-cpp:8080/ketterl/ff_test.html")
        await asyncio.Future()  # läuft bis Strg+C


if __name__ == "__main__":
    asyncio.run(main())
