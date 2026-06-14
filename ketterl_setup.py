"""
ketterl_setup.py – KNX Visu Auto-Setup

Alles in einem Befehl:
    ETS-Export CSV → knx_config.json → Brain MD-Dateien → Zettel-Dashboard HTML

Aufruf:
    python ketterl_setup.py export.csv
    python ketterl_setup.py export.csv --project "CPP Hortigstrasse"
    python ketterl_setup.py export.csv --project "CPP" --address "Musterweg 1, Wien" --knx 192.168.1.100
    python ketterl_setup.py export.csv --no-brain     # ohne Obsidian-Dateien
    python ketterl_setup.py export.csv --open         # Dashboard im Browser öffnen

Raspberry Pi Quickstart:
    sudo python ketterl_setup.py export.csv --autostart --knx 192.168.1.100
"""

from __future__ import annotations
import sys, json, argparse, textwrap
from pathlib import Path


def banner(msg: str):
    print(f"\n{'─'*55}")
    print(f"  {msg}")
    print(f"{'─'*55}")


def step(num: int, total: int, msg: str):
    print(f"\n[{num}/{total}] {msg} ...")


def ok(msg: str):
    print(f"  ✅ {msg}")


def info(msg: str):
    print(f"  ℹ  {msg}")


def warn(msg: str):
    print(f"  ⚠️  {msg}")


# ─── Raspberry Pi Autostart ──────────────────────────────────────────────────
_SYSTEMD_TEMPLATE = """[Unit]
Description=KNX Visu Ketterl
After=network.target

[Service]
WorkingDirectory=@@WORK_DIR@@
ExecStart=python3 knx_server.py --config knx_config.json @@KNX_ARG@@ --zettel
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
"""

def write_autostart(work_dir: Path, knx_ip: str | None):
    knx_arg   = f"--knx {knx_ip}" if knx_ip else "--demo"
    service   = (_SYSTEMD_TEMPLATE
                 .replace("@@WORK_DIR@@", str(work_dir))
                 .replace("@@KNX_ARG@@", knx_arg))
    svc_path  = work_dir / "knx-visu.service"
    svc_path.write_text(service, encoding="utf-8")
    print(f"\n  Service-Datei: {svc_path}")
    print(textwrap.dedent(f"""
  Autostart aktivieren (als root):
    sudo cp {svc_path} /etc/systemd/system/
    sudo systemctl enable knx-visu
    sudo systemctl start knx-visu

  Status prüfen:
    sudo systemctl status knx-visu
"""))


# ─── Haupt ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="ETS CSV → Dashboard + Brain in einem Schritt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Beispiele:
              python ketterl_setup.py mein_export.csv
              python ketterl_setup.py mein_export.csv --project "Villa Muster"
              python ketterl_setup.py mein_export.csv --project "CPP" --open
              python ketterl_setup.py mein_export.csv --autostart --knx 192.168.1.100
        """)
    )
    ap.add_argument("csv",           help="ETS CSV-Export Datei")
    ap.add_argument("--project",     default=None, help="Projektname (Standard: aus CSV-Dateiname)")
    ap.add_argument("--address",     default=None, help="Adresse des Projekts")
    ap.add_argument("--contact",     default="Michael Träumer", help="Kontaktname")
    ap.add_argument("--knx",         default=None, help="KNX IP-Interface IP-Adresse")
    ap.add_argument("--ws-port",     type=int, default=8765, help="WebSocket-Port (Standard: 8765)")
    ap.add_argument("--http-port",   type=int, default=8080, help="HTTP-Port (Standard: 8080)")
    ap.add_argument("--output-dir",  default=None, help="Ausgabeordner (Standard: neben der CSV)")
    ap.add_argument("--brain-dir",   default=None, help="Brain-Basisordner (Standard: ./Brain)")
    ap.add_argument("--no-brain",    action="store_true", help="Brain-Dateien nicht erstellen")
    ap.add_argument("--no-dashboard",action="store_true", help="Dashboard nicht erstellen")
    ap.add_argument("--open",        action="store_true", help="Dashboard nach Erstellung im Browser öffnen")
    ap.add_argument("--autostart",   action="store_true", help="systemd Service-Datei für Raspberry Pi schreiben")
    ap.add_argument("--preview",     action="store_true", help="Parsed Räume anzeigen, nichts speichern")
    ap.add_argument("--mode",        default="auto", choices=["auto","room","gewerk"],
                    help="Parser-Modus: auto=erkennen, room=Raum-Hierarchie, gewerk=Gewerk-Hierarchie")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"FEHLER: Datei nicht gefunden: {csv_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output_dir) if args.output_dir else csv_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # Projektname
    project_name = args.project or csv_path.stem.replace("_", " ").replace("-", " ")

    banner(f"Ketterl Setup  ·  {project_name}")
    print(f"  CSV:     {csv_path}")
    print(f"  Ausgabe: {out_dir}")

    total = 2
    if not args.no_brain:     total += 1
    if not args.no_dashboard: total += 1
    if args.autostart:        total += 1
    step_n = 0

    # ── Schritt 1: ETS parsen ────────────────────────────────────────────────
    step_n += 1
    step(step_n, total, "ETS-Export parsen")

    try:
        import importlib.util, os
        script_dir = Path(__file__).parent

        # Auto-detect: Gewerk-Format hat keine Kopfzeile und 9 Tab-Spalten
        mode = args.mode
        if mode == "auto":
            try:
                first = csv_path.read_bytes()[:500].decode("cp1252", errors="replace")
                # Gewerk-CSV: erste Zeile beginnt mit einem bekannten Gewerk-Wort
                import re
                if re.match(r'^"?(Licht|Sonnenschutz|Heizung|Klima|Lüftung|Diverses|Gewerke|Test)', first):
                    mode = "gewerk"
                else:
                    mode = "room"
            except Exception:
                mode = "room"

        parser_file = "ets_parser_gewerk.py" if mode == "gewerk" else "ets_parser.py"
        info(f"Parser-Modus: {mode}  ({parser_file})")

        spec = importlib.util.spec_from_file_location("ets_parser", script_dir / parser_file)
        ep   = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ep)
    except Exception as ex:
        print(f"  FEHLER: Parser nicht gefunden: {ex}", file=sys.stderr)
        sys.exit(1)

    try:
        if mode == "gewerk":
            config = ep.build_config(csv_path, project_name)
        else:
            config = ep.build_config(csv_path)
    except Exception as ex:
        print(f"  FEHLER beim Parsen: {ex}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)

    meta = config.get("meta", {})
    ok(f"{meta.get('total_rooms','?')} Räume, {meta.get('total_gas','?')} Gruppenadressen")

    # Projekt-Info ergänzen
    if "project" not in config:
        config["project"] = {}
    config["project"]["name"]    = project_name
    if args.address:
        config["project"]["address"] = args.address
    if args.contact:
        config["project"]["contact"] = args.contact

    if args.preview:
        print("\n── Erkannte Räume ───────────────────────────────────")
        for room in config.get("rooms", []):
            devs = room.get("devices", {})
            print(f"  {room.get('floor','?'):5s}  {room.get('name','?'):<25}  "
                  f"({len(devs)} Geräte)")
        print()
        return

    # ── Schritt 2: knx_config.json speichern ────────────────────────────────
    step_n += 1
    step(step_n, total, "knx_config.json schreiben")
    cfg_path = out_dir / "knx_config.json"
    cfg_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    ok(str(cfg_path))

    # ── Schritt 3: Brain MD-Dateien ─────────────────────────────────────────
    if not args.no_brain:
        step_n += 1
        step(step_n, total, "Obsidian Brain MD-Dateien erstellen")
        try:
            spec2 = importlib.util.spec_from_file_location("brain_gen", script_dir / "brain_gen.py")
            bg    = importlib.util.module_from_spec(spec2)
            spec2.loader.exec_module(bg)

            brain_base = Path(args.brain_dir) if args.brain_dir else out_dir / "Brain"
            brain_dir  = bg.generate_brain(cfg_path, brain_base, project_name)
            ok(f"Brain: {brain_dir}")
        except Exception as ex:
            warn(f"Brain-Generierung fehlgeschlagen: {ex}")

    # ── Schritt 4: Zettel-Dashboard ─────────────────────────────────────────
    dash_path = None
    if not args.no_dashboard:
        step_n += 1
        step(step_n, total, "Zettel-Dashboard HTML erstellen")
        try:
            spec3 = importlib.util.spec_from_file_location("dashboard_zettel", script_dir / "dashboard_zettel.py")
            dz    = importlib.util.module_from_spec(spec3)
            spec3.loader.exec_module(dz)

            html      = dz.generate_html(config, ws_port=args.ws_port)
            dash_path = out_dir / "dashboard_zettel.html"
            dash_path.write_text(html, encoding="utf-8")
            ok(f"{dash_path}  ({len(html)//1024} kB)")
        except Exception as ex:
            warn(f"Dashboard-Generierung fehlgeschlagen: {ex}")
            import traceback; traceback.print_exc()

    # ── Schritt 5: Autostart (optional) ─────────────────────────────────────
    if args.autostart:
        step_n += 1
        step(step_n, total, "systemd Service-Datei schreiben")
        write_autostart(out_dir, args.knx)

    # ── Zusammenfassung ──────────────────────────────────────────────────────
    banner("Fertig!")

    if dash_path:
        print(f"\n  Dashboard:  {dash_path}")
        print(f"  Im Browser öffnen:")
        print(f"    python -m http.server 8080 --directory \"{out_dir}\"")
        print(f"    → http://localhost:8080/dashboard_zettel.html\n")
        if args.knx:
            print(f"  Mit KNX-Interface:")
            print(f"    python knx_server.py --config knx_config.json --knx {args.knx} --zettel")
        else:
            print(f"  Demo-Modus (ohne KNX):")
            print(f"    python knx_server.py --config knx_config.json --demo --zettel")
        print()

    if args.open and dash_path and dash_path.exists():
        import webbrowser
        webbrowser.open(dash_path.resolve().as_uri())
        info("Dashboard im Browser geöffnet")


if __name__ == "__main__":
    main()
