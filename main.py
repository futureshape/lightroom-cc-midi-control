#!/usr/bin/env python3
"""
Lightroom MIDI Bridge
─────────────────────
Connect a physical MIDI controller to Adobe Lightroom CC's
built-in external controller WebSocket API.

First-time setup:
    python main.py --configure

Run the bridge:
    python main.py
"""
from __future__ import annotations

import argparse
import asyncio
import sys


# ─────────────────────────────────────────────────────────────── dep check ────

def _check_deps():
    missing = []
    for pkg, mod in [
        ("python-rtmidi", "rtmidi"),
        ("websockets",    "websockets"),
        ("rich",          "rich"),
        ("textual",       "textual"),
    ]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("Missing Python packages:")
        for p in missing:
            print(f"  {p}")
        print(
            "\nInstall with:\n"
            f"  pip3 install {' '.join(missing)} --break-system-packages"
        )
        sys.exit(1)


# ───────────────────────────────────────────────────────────────── entrypoint ─

def main():
    _check_deps()

    parser = argparse.ArgumentParser(
        description="Lightroom MIDI Bridge — map a MIDI controller to Lightroom CC",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python main.py --configure    interactive setup (run this first)
  python main.py                run the bridge with saved mappings
  python main.py --port 7683    override the WebSocket port
        """,
    )
    parser.add_argument(
        "--configure", "-c",
        action="store_true",
        help="Interactive setup: detect MIDI controls and map them to Lightroom",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=None,
        metavar="PORT",
        help=(
            "Lightroom WebSocket port "
            "(default: read from ~/Library/…/Connections/connections.json, "
            "fallback 7682)"
        ),
    )
    args = parser.parse_args()

    if args.configure:
        from configure import run_configure
        try:
            run_configure(args.port)
        except KeyboardInterrupt:
            print("\nSetup cancelled.")
    else:
        from bridge import run_bridge
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        console.print(Panel.fit(
            "[bold blue]Lightroom MIDI Bridge[/bold blue]",
            border_style="blue",
        ))

        try:
            asyncio.run(run_bridge(args.port))
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopped.[/yellow]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            sys.exit(1)


if __name__ == "__main__":
    main()
