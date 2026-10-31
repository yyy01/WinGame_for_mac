"""Local setup CLI. Generated apps run natively without a Python dependency."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__
from .build import build_app
from .config import (
    ConfigError,
    home,
    load_json,
    manifest,
    private_profile,
    write_private,
)
from .core import (
    change_sync,
    compare_inventory,
    doctor,
    game_add,
    inventory,
    runtime_add,
    stop_session,
)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wgfm", description="Local Windows-game compatibility wrappers for macOS"
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument(
        "--home", help="Private configuration root (default: macOS Application Support)"
    )
    sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="Validate a public game manifest")
    v.add_argument("manifest", type=Path)
    r = sub.add_parser("runtime-add", help="Register an already-installed Wine runtime")
    r.add_argument("id")
    r.add_argument("--wine", type=Path, required=True)
    r.add_argument("--wineserver", type=Path, required=True)
    r.add_argument("--library", type=Path, action="append", default=[])
    r.add_argument("--sync", choices=["server", "msync", "esync"], default="server")
    g = sub.add_parser(
        "game-add", help="Import game data; never import a Steam client or Wine prefix"
    )
    g.add_argument("manifest", type=Path)
    g.add_argument("--source", type=Path, required=True)
    g.add_argument("--runtime", required=True)
    g.add_argument(
        "--adopt-existing",
        action="store_true",
        help="Reference existing game data instead of making an isolated copy",
    )
    for name in ["prefix", "steam", "probe", "cover", "icon"]:
        g.add_argument("--" + name, type=Path)
    for name in ["doctor", "stop"]:
        c = sub.add_parser(name)
        c.add_argument("id")
    for name in ["build", "launch"]:
        b = sub.add_parser(name)
        b.add_argument("id")
        b.add_argument("--output", type=Path, default=Path.home() / "Applications")
        b.add_argument("--replace", action="store_true")
    a = sub.add_parser(
        "artwork", help="Set local launcher cover and/or app icon; rebuild to apply"
    )
    a.add_argument("id")
    a.add_argument("--cover", type=Path)
    a.add_argument("--icon", type=Path)
    s = sub.add_parser(
        "sync", help="Stop this Wine session and change its synchronization mode"
    )
    s.add_argument("id")
    s.add_argument("mode", choices=["server", "msync", "esync"])
    i = sub.add_parser(
        "inventory",
        help="Hash a self-contained game directory, excluding private prefixes",
    )
    i.add_argument("source", type=Path)
    i.add_argument("--output", type=Path, required=True)
    c = sub.add_parser("check-inventory")
    c.add_argument("inventory", type=Path)
    c.add_argument("source", type=Path)
    d = sub.add_parser(
        "diagnostics",
        help="Export only allowlisted checks, never raw logs, profiles or account state",
    )
    d.add_argument("id")
    d.add_argument("--output", type=Path, required=True)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    root = home(args.home)
    try:
        c = args.command
        if c == "validate":
            result = manifest(load_json(args.manifest))
        elif c == "runtime-add":
            runtime_add(
                root, args.id, args.wine, args.wineserver, args.library, args.sync
            )
            result = {"runtime_id": args.id, "registered": True}
        elif c == "game-add":
            data = game_add(
                root,
                load_json(args.manifest),
                args.source,
                args.runtime,
                adopt=args.adopt_existing,
                prefix=args.prefix,
                steam=args.steam,
                probe=args.probe,
                cover=args.cover,
                icon=args.icon,
            )
            result = {
                "game_id": data["manifest"]["id"],
                "architecture": data["architecture"],
                "adopted": data["adopted"],
            }
        elif c == "doctor":
            result = doctor(root, args.id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ready_for_launch_check"] else 1
        elif c in {"build", "launch"}:
            app = build_app(root, args.id, args.output, replace=args.replace)
            if c == "launch":
                subprocess.run(["/usr/bin/open", str(app)], check=True)
            result = {"application": str(app)}
        elif c == "artwork":
            if not args.cover and not args.icon:
                raise ConfigError("Select --cover and/or --icon")
            data = private_profile(root, args.id)
            for key in ["cover", "icon"]:
                path = getattr(args, key)
                if path:
                    if not path.is_file():
                        raise ConfigError("Artwork file does not exist")
                    data[key] = str(path.resolve())
            write_private(root / "profiles" / (args.id + ".json"), data)
            result = {
                "game_id": args.id,
                "artwork_updated": True,
                "next_step": "build --replace to apply",
            }
        elif c == "stop":
            stop_session(root, args.id)
            result = {"game_id": args.id, "session_stopped": True}
        elif c == "sync":
            change_sync(root, args.id, args.mode)
            result = {"game_id": args.id, "sync": args.mode}
        elif c == "inventory":
            write_private(
                args.output,
                {"schema_version": 1, "files": inventory(args.source.resolve())},
            )
            result = {"inventory": str(args.output)}
        elif c == "check-inventory":
            result = compare_inventory(
                load_json(args.inventory, max_bytes=128 * 1024 * 1024)["files"],
                args.source.resolve(),
            )
            print(json.dumps(result, indent=2))
            return 0 if result["passed"] else 1
        elif c == "diagnostics":
            result = {
                "component_version": __version__,
                "installation": doctor(root, args.id),
            }
            write_private(args.output, result)
            result = {"diagnostics": str(args.output)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        ConfigError,
        OSError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"wgfm: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
