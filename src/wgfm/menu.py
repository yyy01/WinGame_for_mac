"""Opt-in GoldSrc menu artwork with exact original-layout restoration.

Stock textures and loading layouts remain untouched. Only supported mods get
two local layout overrides and a new, uniquely named texture directory.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .build import compile_launcher
from .config import ConfigError, load_json, private_profile, within, write_private
from .core import inventory, session_lock, sha256

LAYOUTS = ("BackgroundLayout.txt", "HD_BackgroundLayout.txt")


def _mod(profile: dict) -> Path:
    m = profile["manifest"]
    args = m["arguments"]
    try:
        mod = args[args.index("-game") + 1]
    except (ValueError, IndexError):
        raise ConfigError(
            "GoldSrc adapter requires an explicit -game cstrike/czero argument"
        ) from None
    if Path(m["executable"]).name.lower() != "hl.exe" or mod not in {
        "cstrike",
        "czero",
    }:
        raise ConfigError(
            "Menu background adapter currently supports CS 1.6 and Condition Zero only"
        )
    path = within(Path(profile["game_path"]), mod)
    if not path.is_dir():
        raise ConfigError("GoldSrc mod directory is missing")
    return path


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".wgfm-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def make_background(
    root: Path, image: Path, destination: Path, width: int, height: int
) -> None:
    subprocess.run(
        [
            str(compile_launcher(root)),
            "--make-goldsrc-background",
            str(image),
            str(destination),
            str(width),
            str(height),
        ],
        check=True,
    )


def set_menu(
    root: Path, game_id: str, image: Path, width: int = 2048, height: int = 1280
) -> dict:
    if not image.is_file() or image.stat().st_size > 32 * 1024 * 1024:
        raise ConfigError("Background image must exist and be at most 32 MiB")
    if any(
        type(n) is not int or n < 256 or n > 4096 or n % 256 for n in [width, height]
    ):
        raise ConfigError(
            "Background dimensions must be multiples of 256, between 256 and 4096"
        )
    p = private_profile(root, game_id)
    mod = _mod(p)
    backup = root / "backups" / game_id / "goldsrc-menu"
    with session_lock(root, p):
        if backup.exists():
            raise ConfigError(
                "Restore the current menu background before applying another image"
            )
        if (mod / "resource").is_symlink() or any(
            (mod / "resource" / name).is_symlink() for name in LAYOUTS
        ):
            raise ConfigError(
                "Menu layout overrides cannot follow shared resource symlinks"
            )
        paths = {name: within(mod, "resource/" + name) for name in LAYOUTS}
        originals = {
            name: path.read_bytes() if path.exists() else None
            for name, path in paths.items()
        }
        if any(b is not None and len(b) > 1024 * 1024 for b in originals.values()):
            raise ConfigError("Unexpected background layout size")
        generation = hashlib.sha256(
            (sha256(image) + f":{width}:{height}").encode()
        ).hexdigest()[:20]
        relative = "resource/background/wgfm_menu_" + generation
        target = within(mod, relative)
        if target.exists():
            raise ConfigError(
                "Background asset directory already exists; no files were overwritten"
            )
        with tempfile.TemporaryDirectory(prefix="wgfm-menu-") as tmp:
            tiles = Path(tmp) / "tiles"
            make_background(root, image, tiles, width, height)
            expected = {
                f"tile_{x}_{y}.tga"
                for y in range(height // 256)
                for x in range(width // 256)
            }
            if {f.name for f in tiles.iterdir()} != expected:
                raise ConfigError(
                    "Background encoder did not produce the expected tiles"
                )
            lines = [f"resolution\t{width}\t{height}", ""]
            for y in range(height // 256):
                for x in range(width // 256):
                    lines.append(
                        f"{relative}/tile_{x}_{y}.tga\tscaled\t{x * 256}\t{y * 256}"
                    )
            layout = ("\n".join(lines) + "\n").encode()
            backup.mkdir(parents=True, mode=0o700)
            try:
                state = {
                    "schema_version": 1,
                    "texture_directory": relative,
                    "tiles": inventory(tiles),
                    "applied_layout_sha256": hashlib.sha256(layout).hexdigest(),
                    "originals": {},
                }
                for name, data in originals.items():
                    state["originals"][name] = (
                        None if data is None else hashlib.sha256(data).hexdigest()
                    )
                    if data is not None:
                        _atomic_bytes(backup / (name + ".original"), data)
                # Persist recovery data before modifying the game. A crash still leaves originals available.
                write_private(backup / "state.json", state)
            except BaseException:
                shutil.rmtree(backup)
                raise
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(tiles, target)
                for path in paths.values():
                    _atomic_bytes(path, layout)
            except BaseException:
                for name, path in paths.items():
                    if originals[name] is None:
                        path.unlink(missing_ok=True)
                    else:
                        _atomic_bytes(path, originals[name])
                if target.exists():
                    shutil.rmtree(target)
                shutil.rmtree(backup)
                raise
    return {
        "game_id": game_id,
        "menu_background_applied": True,
        "size": [width, height],
        "tiles": len(expected),
        "restart_required": True,
    }


def restore_menu(root: Path, game_id: str) -> dict:
    p = private_profile(root, game_id)
    mod = _mod(p)
    backup = root / "backups" / game_id / "goldsrc-menu"
    with session_lock(root, p):
        if not (backup / "state.json").is_file():
            raise ConfigError("No managed menu background is installed")
        state = load_json(backup / "state.json")
        relative = state["texture_directory"]
        if not re.fullmatch(r"resource/background/wgfm_menu_[a-f0-9]{20}", relative):
            raise ConfigError("Invalid managed background directory")
        paths = {name: within(mod, "resource/" + name) for name in LAYOUTS}
        original = {}
        for name, path in paths.items():
            digest = state["originals"][name]
            actual = sha256(path) if path.is_file() else None
            if actual not in {state["applied_layout_sha256"], digest}:
                raise ConfigError(
                    "Menu layout changed since applying artwork; preserve those edits before restoring"
                )
            digest = state["originals"][name]
            data = (
                None if digest is None else (backup / (name + ".original")).read_bytes()
            )
            if data is not None and hashlib.sha256(data).hexdigest() != digest:
                raise ConfigError("Original menu backup failed its hash check")
            original[name] = data
        # Keep both applied layouts if restoration encounters an I/O failure.
        applied = {
            name: path.read_bytes() if path.exists() else None
            for name, path in paths.items()
        }
        try:
            for name, path in paths.items():
                if original[name] is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_bytes(path, original[name])
        except BaseException:
            for name, path in paths.items():
                if applied[name] is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_bytes(path, applied[name])
            raise
        preserved = False
        try:
            target = within(mod, relative)
            if target.exists():
                if not target.is_symlink() and inventory(target) == state["tiles"]:
                    shutil.rmtree(target)
                else:
                    preserved = True
        except ConfigError:
            preserved = True
        shutil.rmtree(backup)
    return {
        "game_id": game_id,
        "original_menu_restored": True,
        "modified_texture_files_preserved": preserved,
        "restart_required": True,
    }
