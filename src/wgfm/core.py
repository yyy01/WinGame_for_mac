from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import platform
import shutil
import struct
import subprocess
from pathlib import Path

from .config import (
    ConfigError,
    identifier,
    load_json,
    manifest,
    private_profile,
    within,
    write_private,
)


def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return (
            hashlib.file_digest(f, "sha256").hexdigest()
            if hasattr(hashlib, "file_digest")
            else _hash_stream(f)
        )


def _hash_stream(f) -> str:
    h = hashlib.sha256()
    for block in iter(lambda: f.read(1024 * 1024), b""):
        h.update(block)
    return h.hexdigest()


def pe_architecture(path: Path) -> str:
    with path.open("rb") as f:
        head = f.read(64)
        if len(head) != 64 or head[:2] != b"MZ":
            raise ConfigError("Executable is not a Windows PE file")
        offset = struct.unpack_from("<I", head, 60)[0]
        if offset > 1024 * 1024:
            raise ConfigError("Invalid PE header offset")
        f.seek(offset)
        header = f.read(6)
    if len(header) != 6 or header[:4] != b"PE\0\0":
        raise ConfigError("Invalid PE signature")
    machine = struct.unpack_from("<H", header, 4)[0]
    if machine not in {0x14C, 0x8664}:
        raise ConfigError("This runtime adapter supports x86/x64 Windows executables")
    return {0x14C: "x86", 0x8664: "x64"}[machine]


def runtime_add(
    root: Path,
    runtime_id: str,
    wine: Path,
    server: Path,
    libraries: list[Path],
    sync: str,
) -> dict:
    identifier(runtime_id)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    if sync not in {"server", "msync", "esync"}:
        raise ConfigError("sync must be server, msync or esync")
    for p in [wine, server]:
        if not p.is_file() or not os.access(p, os.X_OK):
            raise ConfigError(f"Runtime executable missing or not executable: {p.name}")
    if any(not p.is_dir() for p in libraries):
        raise ConfigError("Runtime library directory does not exist")
    path = root / "runtimes" / (runtime_id + ".json")
    if path.exists():
        raise ConfigError(
            "Runtime ID already exists; use a new ID for a new runtime revision"
        )
    data = {
        "schema_version": 1,
        "id": runtime_id,
        "wine": str(wine.resolve()),
        "wineserver": str(server.resolve()),
        "library_paths": [str(p.resolve()) for p in libraries],
        "sync": sync,
        "wine_sha256": sha256(wine),
        "server_sha256": sha256(server),
    }
    # Retain the .app loader path: resolving its symlink can lose Cocoa bundle metadata.
    data["wine"] = str(wine.absolute())
    write_private(path, data)
    return data


def runtime(root: Path, runtime_id: str) -> dict:
    data = load_json(root / "runtimes" / (identifier(runtime_id) + ".json"))
    if data.get("id") != runtime_id or data.get("sync") not in {
        "server",
        "msync",
        "esync",
    }:
        raise ConfigError("Invalid runtime descriptor")
    for key in ["wine", "wineserver"]:
        if not isinstance(data.get(key), str) or not Path(data[key]).is_absolute():
            raise ConfigError("Runtime paths must be absolute")
    if not isinstance(data.get("library_paths"), list) or any(
        not isinstance(p, str) or not Path(p).is_absolute()
        for p in data["library_paths"]
    ):
        raise ConfigError("Runtime library paths must be absolute")
    return data


def validate_source(source: Path) -> None:
    if not source.is_dir():
        raise ConfigError("Game source must be a directory")
    if (source / "system.reg").exists() and (source / "dosdevices").exists():
        raise ConfigError("A Wine prefix is private state, not a game-data source")
    if (source / "steam.exe").exists() and (source / "config").is_dir():
        raise ConfigError(
            "Import only the game directory, not the Steam client or login environment"
        )
    for current, dirs, files in os.walk(source, followlinks=False):
        for name in dirs + files:
            p = Path(current) / name
            if p.is_symlink() and not p.resolve().is_relative_to(source.resolve()):
                raise ConfigError(
                    "Game source contains an external symlink; select a self-contained source"
                )


def clone_game(source: Path, destination: Path) -> str:
    validate_source(source)
    if destination.exists():
        raise ConfigError("Destination already exists; no game files were replaced")
    if destination.resolve().is_relative_to(source.resolve()):
        raise ConfigError("Game destination cannot be inside its source")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    method = "copy"
    try:
        if platform.system() == "Darwin":
            p = subprocess.run(
                ["/bin/cp", "-cR", str(source), str(destination)],
                capture_output=True,
                check=False,
            )
            if p.returncode == 0:
                method = "apfs_clone"
            else:
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(source, destination, symlinks=True)
        else:
            shutil.copytree(source, destination, symlinks=True)
        return method
    except BaseException:
        if destination.exists():
            shutil.rmtree(destination)
        raise


def game_add(
    root: Path,
    definition: dict,
    source: Path,
    runtime_id: str,
    *,
    adopt: bool = False,
    prefix: Path | None = None,
    steam: Path | None = None,
    probe: Path | None = None,
    cover: Path | None = None,
    icon: Path | None = None,
) -> dict:
    definition = manifest(definition)
    rt = runtime(root, runtime_id)
    game_id = definition["id"]
    target = root / "profiles" / (game_id + ".json")
    if target.exists():
        raise ConfigError("Game ID already exists; source files were not touched")
    source = source.resolve()
    exe = within(source, definition["executable"])
    architecture = pe_architecture(exe)
    if not within(source, definition["working_directory"]).is_dir():
        raise ConfigError("Game working directory does not exist")
    if definition.get("steam_app_id") and (steam is None or probe is None):
        raise ConfigError(
            "Steam games require explicit local Steam executable and matching x86/x64 readiness probe"
        )
    for p in [steam, probe, cover, icon]:
        if p is not None and not p.is_file():
            raise ConfigError(f"Selected local file is missing: {p.name}")
    if probe is not None and pe_architecture(probe) != architecture:
        raise ConfigError("Steam readiness probe architecture must match the game")
    validate_source(source)
    game = source if adopt else root / "games" / game_id / "Game"
    prefix = (prefix or root / "sessions" / game_id / "Prefix").resolve()
    # Synchronization belongs to a prefix, not to an individual game. Never mix live modes.
    session_id = hashlib.sha256(str(prefix).encode()).hexdigest()[:24]
    session_path = root / "sessions" / (session_id + ".json")
    expected = {"prefix": str(prefix), "runtime_id": runtime_id, "sync": rt["sync"]}
    if session_path.exists() and load_json(session_path) != expected:
        raise ConfigError(
            "This prefix is registered with another runtime/sync mode; stop it before migrating"
        )
    data = {
        "schema_version": 1,
        "manifest": definition,
        "runtime_id": runtime_id,
        "game_path": str(game),
        "prefix": str(prefix),
        "session_id": session_id,
        "architecture": architecture,
        "adopted": adopt,
        "executable_sha256": sha256(exe),
    }
    for key, value in [
        ("steam_executable", steam),
        ("steam_probe", probe),
        ("cover", cover),
        ("icon", icon),
    ]:
        if value is not None:
            data[key] = str(value.resolve())
    # Validate first; failed registration must not leave a full game copy behind.
    created_session = not session_path.exists()
    if not adopt:
        clone_game(source, game)
    try:
        write_private(session_path, expected)
        write_private(target, data)
    except BaseException:
        if not adopt:
            shutil.rmtree(game)
        if created_session:
            session_path.unlink(missing_ok=True)
        raise
    return data


def environment(rt: dict, profile: dict) -> dict:
    # Do not hand shell/API tokens or injected libraries to a game process.
    inherited = {
        k: os.environ[k]
        for k in ["HOME", "USER", "LOGNAME", "PATH", "TMPDIR", "LANG", "LC_ALL"]
        if k in os.environ
    }
    inherited.update(
        {
            "WINEPREFIX": profile["prefix"],
            "WINELOADER": rt["wine"],
            "WINESERVER": rt["wineserver"],
            "WINEDEBUG": "-all",
            "MVK_CONFIG_LOG_LEVEL": "0",
            "WINEMSYNC": "1" if rt["sync"] == "msync" else "0",
            "WINEESYNC": "1" if rt["sync"] == "esync" else "0",
            "DYLD_FALLBACK_LIBRARY_PATH": ":".join(rt["library_paths"] + ["/usr/lib"]),
        }
    )
    inherited.update(profile["manifest"].get("environment", {}))
    appid = profile["manifest"].get("steam_app_id")
    if appid:
        inherited.update(SteamAppId=appid, SteamGameId=appid)
    return inherited


@contextlib.contextmanager
def session_lock(root: Path, profile: dict):
    path = root / "sessions" / (profile["session_id"] + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigError("Another game or maintenance task is using this prefix")
        yield


def doctor(root: Path, game_id: str) -> dict:
    p = private_profile(root, game_id)
    rt = runtime(root, p["runtime_id"])
    game = Path(p["game_path"])
    exe = within(game, p["manifest"]["executable"])
    checks = {
        "macos_host": platform.system() == "Darwin",
        "wine_exists": Path(rt["wine"]).is_file(),
        "wineserver_exists": Path(rt["wineserver"]).is_file(),
        "runtime_wine_unchanged": Path(rt["wine"]).is_file()
        and sha256(Path(rt["wine"])) == rt["wine_sha256"],
        "runtime_server_unchanged": Path(rt["wineserver"]).is_file()
        and sha256(Path(rt["wineserver"])) == rt["server_sha256"],
        "library_directories": all(Path(x).is_dir() for x in rt["library_paths"]),
        "game_exists": exe.is_file(),
        "game_executable_unchanged": exe.is_file()
        and sha256(exe) == p["executable_sha256"],
        "working_directory": within(game, p["manifest"]["working_directory"]).is_dir(),
    }
    session = load_json(root / "sessions" / (p["session_id"] + ".json"))
    checks["session_consistent"] = session == {
        "prefix": p["prefix"],
        "runtime_id": p["runtime_id"],
        "sync": rt["sync"],
    }
    if p["manifest"].get("steam_app_id"):
        checks.update(
            steam_executable=Path(p["steam_executable"]).is_file(),
            steam_probe=Path(p["steam_probe"]).is_file(),
        )
    return {
        "game_id": game_id,
        "architecture": p["architecture"],
        "sync": rt["sync"],
        "checks": checks,
        "ready_for_launch_check": all(checks.values()),
        "note": "File checks are not proof of game compatibility or performance",
    }


def inventory(source: Path) -> dict:
    validate_source(source)
    # Hash game assets only. Prefixes/Steam client roots were rejected above.
    return {
        str(p.relative_to(source)): sha256(p)
        for p in sorted(source.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


def compare_inventory(expected: dict, source: Path) -> dict:
    missing = []
    different = []
    for name, digest in expected.items():
        p = within(source, name)
        if not p.is_file():
            missing.append(name)
        elif sha256(p) != digest:
            different.append(name)
    return {
        "checked": len(expected),
        "missing": missing,
        "different": different,
        "passed": not missing and not different,
    }


def _stop_locked(rt: dict, p: dict) -> None:
    env = environment(rt, p)
    if p.get("steam_executable"):
        try:
            subprocess.run(
                [rt["wine"], p["steam_executable"], "-shutdown"],
                check=False,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            # Give the authenticated client time to flush its private state.
            subprocess.run(
                [rt["wineserver"], "-w"],
                check=False,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
            return
        except subprocess.TimeoutExpired:
            pass
    subprocess.run([rt["wineserver"], "-k"], env=env, check=True, timeout=10)
    subprocess.run([rt["wineserver"], "-w"], env=env, check=True, timeout=10)


def stop_session(root: Path, game_id: str) -> None:
    p = private_profile(root, game_id)
    rt = runtime(root, p["runtime_id"])
    with session_lock(root, p):
        _stop_locked(rt, p)


def change_sync(root: Path, game_id: str, mode: str) -> None:
    if mode not in {"server", "msync", "esync"}:
        raise ConfigError("Invalid sync mode")
    p = private_profile(root, game_id)
    rt = runtime(root, p["runtime_id"])
    # A new descriptor avoids silently changing another prefix using the old runtime.
    new_id = identifier(rt["id"][:53] + "-" + mode)
    new = dict(rt, id=new_id, sync=mode)
    existing = root / "runtimes" / (new_id + ".json")
    if existing.exists() and load_json(existing) != new:
        raise ConfigError("Runtime revision collision")
    with session_lock(root, p):
        _stop_locked(rt, p)
        write_private(existing, new)
        # Update every registered game deliberately sharing this prefix.
        for file in (root / "profiles").glob("*.json"):
            other = load_json(file)
            if other.get("prefix") == p["prefix"]:
                other["runtime_id"] = new_id
                write_private(file, other)
        write_private(
            root / "sessions" / (p["session_id"] + ".json"),
            {"prefix": p["prefix"], "runtime_id": new_id, "sync": mode},
        )
