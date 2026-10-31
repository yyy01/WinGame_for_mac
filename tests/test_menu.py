import unittest
from pathlib import Path
from unittest.mock import patch

import test_component as fixtures

from wgfm.config import ConfigError
from wgfm.core import game_add, session_lock
from wgfm.menu import restore_menu, set_menu


class MenuTests(unittest.TestCase):
    setUp = fixtures.ComponentTests.setUp
    tearDown = fixtures.ComponentTests.tearDown

    # Reuse the isolated source/runtime fixture, not the base test methods.
    def register_menu(self):
        (self.source / "Game.exe").rename(self.source / "hl.exe")
        resource = self.source / "cstrike/resource"
        resource.mkdir(parents=True)
        (resource / "BackgroundLayout.txt").write_bytes(b"original layout\r\n")
        (resource / "BackgroundLoadingLayout.txt").write_text("loading stays original")
        (resource / "stock.tga").write_bytes(b"original texture")
        return game_add(
            self.root,
            dict(
                fixtures.definition(arguments=["-game", "cstrike"]), executable="hl.exe"
            ),
            self.source,
            "test",
        )

    def image(self):
        path = self.base / "image.png"
        path.write_bytes(b"image")
        return path

    @staticmethod
    def encode(root, image, destination, width, height):
        destination.mkdir()
        for y in range(height // 256):
            for x in range(width // 256):
                (destination / f"tile_{x}_{y}.tga").write_bytes(bytes([x, y]))

    def apply(self):
        with patch("wgfm.menu.make_background", side_effect=self.encode):
            return set_menu(self.root, "sample", self.image(), 512, 256)

    def test_menu_overrides_restore_exact_original_and_absent_hd_file(self):
        p = self.register_menu()
        res = Path(p["game_path"]) / "cstrike/resource"
        self.apply()
        self.assertNotEqual(
            (res / "BackgroundLayout.txt").read_bytes(), b"original layout\r\n"
        )
        self.assertTrue((res / "HD_BackgroundLayout.txt").exists())
        self.assertEqual((res / "stock.tga").read_bytes(), b"original texture")
        self.assertEqual(
            (res / "BackgroundLoadingLayout.txt").read_text(), "loading stays original"
        )
        restore_menu(self.root, "sample")
        self.assertEqual(
            (res / "BackgroundLayout.txt").read_bytes(), b"original layout\r\n"
        )
        self.assertFalse((res / "HD_BackgroundLayout.txt").exists())
        self.assertEqual(list((res / "background").iterdir()), [])
        self.assertEqual(
            (self.source / "cstrike/resource/BackgroundLayout.txt").read_bytes(),
            b"original layout\r\n",
        )

    def test_menu_restore_refuses_to_overwrite_user_layout_edit(self):
        p = self.register_menu()
        self.apply()
        res = Path(p["game_path"]) / "cstrike/resource"
        (res / "BackgroundLayout.txt").write_text("user edit")
        with self.assertRaises(ConfigError):
            restore_menu(self.root, "sample")
        self.assertEqual((res / "BackgroundLayout.txt").read_text(), "user edit")

    def test_menu_restore_preserves_user_modified_generated_textures(self):
        p = self.register_menu()
        self.apply()
        res = Path(p["game_path"]) / "cstrike/resource"
        tile = next((res / "background").rglob("*.tga"))
        tile.write_text("user edit")
        self.assertTrue(
            restore_menu(self.root, "sample")["modified_texture_files_preserved"]
        )
        self.assertEqual(tile.read_text(), "user edit")

    def test_menu_operations_reject_running_session(self):
        p = self.register_menu()
        with session_lock(self.root, p), self.assertRaises(ConfigError):
            self.apply()

    def test_menu_requires_restore_before_another_change(self):
        self.register_menu()
        self.apply()
        with self.assertRaises(ConfigError):
            self.apply()

    def test_menu_refuses_corrupted_backup(self):
        p = self.register_menu()
        self.apply()
        res = Path(p["game_path"]) / "cstrike/resource"
        before = (res / "BackgroundLayout.txt").read_bytes()
        (
            self.root / "backups/sample/goldsrc-menu/BackgroundLayout.txt.original"
        ).write_text("bad backup")
        with self.assertRaises(ConfigError):
            restore_menu(self.root, "sample")
        self.assertEqual((res / "BackgroundLayout.txt").read_bytes(), before)

    def test_menu_recovers_an_interrupted_layout_replacement(self):
        p = self.register_menu()
        self.apply()
        res = Path(p["game_path"]) / "cstrike/resource"
        # Simulate power loss before the second layout override was committed.
        (res / "HD_BackgroundLayout.txt").unlink()
        restore_menu(self.root, "sample")
        self.assertEqual(
            (res / "BackgroundLayout.txt").read_bytes(), b"original layout\r\n"
        )
        self.assertFalse((res / "HD_BackgroundLayout.txt").exists())
