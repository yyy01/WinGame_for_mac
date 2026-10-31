"""Strict public manifests; machine-specific profiles stay outside the checkout."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath


class ConfigError(ValueError):
    pass


IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SECRET_KEY = re.compile(
    r"token|password|secret|cookie|credential|private.?key|session", re.IGNORECASE
)
RESERVED_ENV = {
    "WINEPREFIX",
    "WINELOADER",
    "WINESERVER",
    "WINEMSYNC",
    "WINEESYNC",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "HOME",
    "PATH",
}


def identifier(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ConfigError(
            "Identifier must contain lowercase letters, numbers, '_' or '-' (1–64 characters)"
        )
    return value


def relative_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or ":" in value
        or "\x00" in value
    ):
        raise ConfigError("Use a nonempty POSIX relative path")
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts:
        raise ConfigError("Path must stay within the game directory")
    return str(p)


def within(root: Path, relative: str) -> Path:
    p = (root / relative_path(relative)).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ConfigError("Symlink escapes the game directory")
    return p


def load_json(path: Path, max_bytes: int = 1024 * 1024) -> dict:
    if path.stat().st_size > max_bytes:
        raise ConfigError("JSON file exceeds the size limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ConfigError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError("Configuration must be valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise ConfigError("Configuration must be a JSON object")
    return data


def manifest(data: dict) -> dict:
    allowed = {
        "schema_version",
        "id",
        "name",
        "version",
        "executable",
        "working_directory",
        "arguments",
        "environment",
        "steam_app_id",
        "readiness_timeout_seconds",
        "notes",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError("Unknown manifest fields: " + ", ".join(sorted(unknown)))
    if "notes" in data and not isinstance(data["notes"], str):
        raise ConfigError("notes must be a string")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ConfigError("schema_version must be 1")
    identifier(data.get("id"))
    for key in ["name", "version", "executable"]:
        if (
            not isinstance(data.get(key), str)
            or not data[key].strip()
            or "\x00" in data[key]
        ):
            raise ConfigError(f"{key} must be a nonempty string")
    if (
        any(c in data["name"] for c in "/\\:")
        or any(ord(c) < 32 for c in data["name"])
        or data["name"] in {".", ".."}
        or len(data["name"]) > 100
    ):
        raise ConfigError(
            "name must be a valid macOS application name (up to 100 characters)"
        )
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,2}", data["version"]):
        raise ConfigError("version must contain one to three numeric components")
    result = dict(data)
    result["executable"] = relative_path(data["executable"])
    result["working_directory"] = relative_path(data.get("working_directory", "."))
    args = data.get("arguments", [])
    if (
        not isinstance(args, list)
        or len(args) > 128
        or any(not isinstance(a, str) or "\x00" in a for a in args)
    ):
        raise ConfigError(
            "arguments must be an array of up to 128 strings, never a shell command"
        )
    result["arguments"] = args
    env = data.get("environment", {})
    if not isinstance(env, dict):
        raise ConfigError("environment must be an object")
    for key, value in env.items():
        if (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            or SECRET_KEY.search(key)
            or key.upper() in RESERVED_ENV
        ):
            raise ConfigError(f"Reserved or sensitive environment key: {key}")
        if (
            not isinstance(value, str)
            or "\x00" in value
            or value.startswith(("ghp_", "github_pat_", "sk-"))
        ):
            raise ConfigError(f"Invalid environment value for {key}")
    result["environment"] = env
    appid = data.get("steam_app_id")
    if appid is not None and (
        not isinstance(appid, str) or not re.fullmatch(r"[1-9][0-9]{0,9}", appid)
    ):
        raise ConfigError("steam_app_id must be a numeric string")
    timeout = data.get("readiness_timeout_seconds", 120)
    if type(timeout) is not int or not 5 <= timeout <= 300:
        raise ConfigError("readiness_timeout_seconds must be between 5 and 300")
    result["readiness_timeout_seconds"] = timeout
    return result


def home(path: str | None = None) -> Path:
    return (
        Path(path).expanduser().resolve()
        if path
        else Path.home() / "Library/Application Support/WinGameForMac"
    )


def write_private(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def private_profile(root: Path, game_id: str) -> dict:
    p = load_json(root / "profiles" / (identifier(game_id) + ".json"))
    p["manifest"] = manifest(p["manifest"])
    if p["manifest"]["id"] != game_id:
        raise ConfigError("Profile game ID does not match its filename")
    identifier(p["runtime_id"])
    identifier(p["session_id"])
    for key in ["game_path", "prefix"]:
        if not isinstance(p.get(key), str) or not Path(p[key]).is_absolute():
            raise ConfigError("Private profile paths must be absolute")
    return p
