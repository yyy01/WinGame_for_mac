"""macOS integration: compile/sign, artwork encoding, literal argv and lifecycle.

Uses a fake Wine executable, not a game or a Steam account. Game compatibility
still requires the manual checks in docs/VALIDATION.md.
"""

import base64
import platform
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_component import definition, pe

from wgfm.build import build_app
from wgfm.core import game_add, runtime_add, session_lock


@unittest.skipUnless(
    platform.system() == "Darwin", "Native Cocoa integration requires macOS"
)
class NativeTests(unittest.TestCase):
    def test_native_app_artwork_literal_arguments_and_clean_exit(self):
        with tempfile.TemporaryDirectory(prefix="wgfm-native-") as tmp:
            base = Path(tmp)
            root = base / "private home"
            source = base / "game source"
            source.mkdir()
            pe(source / "Game.exe")
            marker = base / "arguments.txt"
            wine = base / "fake wine"
            wine.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$@" > "$WGFM_RECORD"\nsleep 1\nexit 0\n'
            )
            wine.chmod(0o755)
            runtime_add(root, "fake", wine, wine, [], "server")
            image = base / "image.png"
            image.write_bytes(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
                )
            )
            args = [
                "argument with spaces",
                "$(touch SHOULD_NOT_EXIST)",
                "; exit",
                "中文",
            ]
            p = game_add(
                root,
                definition(arguments=args, environment={"WGFM_RECORD": str(marker)}),
                source,
                "fake",
                cover=image,
                icon=image,
            )
            app = build_app(root, "sample", base / "apps")
            res = app / "Contents/Resources"
            self.assertEqual((res / "game.icns").read_bytes()[:4], b"icns")
            self.assertEqual((res / "cover.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            self.assertEqual(info["CFBundleIconFile"], "game.icns")
            subprocess.run(
                [str(app / "Contents/MacOS/launcher")],
                check=True,
                timeout=20,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.assertEqual(
                marker.read_text().splitlines(),
                [str(Path(p["game_path"]) / "Game.exe")] + args,
            )
            self.assertFalse((Path(p["game_path"]) / "SHOULD_NOT_EXIST").exists())
            # A completed launcher must release its lock, allowing restart/maintenance.
            with session_lock(root, p):
                pass
            self.assertLess((root / "logs/sample.log").stat().st_size, 2 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
