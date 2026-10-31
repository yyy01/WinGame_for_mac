from __future__ import annotations

import hashlib
import platform
import plistlib
import shutil
import subprocess
import tempfile
from importlib.resources import files
from pathlib import Path

from .config import ConfigError, private_profile
from .core import doctor


def compile_launcher(root: Path) -> Path:
    if platform.system() != "Darwin":
        raise ConfigError(
            "Native app building requires macOS and Xcode Command Line Tools"
        )
    source = files("wgfm").joinpath("resources/launcher.m").read_bytes()
    digest = hashlib.sha256(source + platform.machine().encode()).hexdigest()[:20]
    directory = root / "cache/bin"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = directory / ("launcher-" + digest)
    if not binary.exists():
        with tempfile.TemporaryDirectory(prefix="wgfm-build-") as tmp:
            src = Path(tmp) / "launcher.m"
            src.write_bytes(source)
            subprocess.run(
                [
                    "/usr/bin/clang",
                    "-arch",
                    platform.machine(),
                    "-mmacosx-version-min=11.0",
                    "-fobjc-arc",
                    "-O2",
                    "-framework",
                    "Cocoa",
                    "-framework",
                    "ImageIO",
                    str(src),
                    "-o",
                    str(binary),
                ],
                check=True,
            )
    return binary


def artwork(source: Path, resources: Path, binary: Path, *, icon: bool) -> None:
    if not source.is_file():
        raise ConfigError("Artwork file does not exist")
    if source.stat().st_size > 32 * 1024 * 1024:
        raise ConfigError("Artwork must be at most 32 MiB")
    if icon and source.suffix.lower() == ".icns":
        if source.read_bytes()[:4] != b"icns":
            raise ConfigError("Invalid ICNS header")
        shutil.copy2(source, resources / "game.icns")
        return
    # Format conversion is a deterministic local build step; game resources are untouched.
    with tempfile.TemporaryDirectory(prefix="wgfm-artwork-") as tmp:
        if icon:
            iconset = Path(tmp) / "game.iconset"
            subprocess.run(
                [str(binary), "--make-icon", str(source), str(iconset)], check=True
            )
            subprocess.run(
                [
                    "/usr/bin/iconutil",
                    "--convert",
                    "icns",
                    "--output",
                    str(resources / "game.icns"),
                    str(iconset),
                ],
                check=True,
            )
        else:
            subprocess.run(
                [
                    "/usr/bin/sips",
                    "-s",
                    "format",
                    "png",
                    "-Z",
                    "1600",
                    str(source),
                    "--out",
                    str(resources / "cover.png"),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )


def build_app(root: Path, game_id: str, output: Path, *, replace: bool = False) -> Path:
    p = private_profile(root, game_id)
    m = p["manifest"]
    result = doctor(root, game_id)
    if not result["ready_for_launch_check"]:
        raise ConfigError("Installation checks failed; run doctor before building")
    binary = compile_launcher(root)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (m["name"] + ".app")
    if target.exists() and not replace:
        raise ConfigError(
            "Application exists; use --replace to keep a backup and rebuild"
        )
    with tempfile.TemporaryDirectory(prefix="wgfm-app-", dir=output) as tmp:
        app = Path(tmp) / target.name
        mac = app / "Contents/MacOS"
        res = app / "Contents/Resources"
        mac.mkdir(parents=True)
        res.mkdir()
        shutil.copy2(binary, mac / "launcher")
        if p.get("cover"):
            artwork(Path(p["cover"]), res, binary, icon=False)
        if p.get("icon"):
            artwork(Path(p["icon"]), res, binary, icon=True)
        info = {
            "CFBundleIdentifier": "local.wgfm." + game_id.replace("_", "-"),
            "CFBundleExecutable": "launcher",
            "CFBundleName": m["name"],
            "CFBundleDisplayName": m["name"],
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": m["version"],
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "WGFMGameID": game_id,
        }
        default = Path.home() / "Library/Application Support/WinGameForMac"
        if root.resolve() != default.resolve():
            info["WGFMHome"] = str(root)
        if p.get("icon"):
            info["CFBundleIconFile"] = "game.icns"
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", str(app)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["/usr/bin/codesign", "--verify", "--strict", str(app)],
            check=True,
            capture_output=True,
        )
        if target.exists():
            backup = target.with_name(target.name + ".previous")
            if backup.exists():
                raise ConfigError(
                    "Previous backup exists; move it aside before another replacement"
                )
            target.rename(backup)
            try:
                shutil.move(str(app), target)
            except BaseException:
                backup.rename(target)
                raise
        else:
            shutil.move(str(app), target)
    return target
