import contextlib
import io
import os
import stat
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wgfm.cli import main
from wgfm.config import ConfigError, load_json, manifest, write_private
from wgfm.core import (
    clone_game,
    compare_inventory,
    doctor,
    environment,
    game_add,
    inventory,
    pe_architecture,
    runtime_add,
    session_lock,
    validate_source,
)


def pe(path, machine=0x14C):
    b = bytearray(128)
    b[:2] = b"MZ"
    struct.pack_into("<I", b, 60, 64)
    b[64:68] = b"PE\0\0"
    struct.pack_into("<H", b, 68, machine)
    path.write_bytes(b)


def definition(**extra):
    return dict(
        schema_version=1,
        id="sample",
        name="Sample Mac",
        version="1.0",
        executable="Game.exe",
        **extra,
    )


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "private root"
        self.source = self.base / "game source"
        self.source.mkdir()
        pe(self.source / "Game.exe")
        (self.source / "settings.cfg").write_text("original")
        self.wine = self.base / "wine"
        self.wine.write_text("#!/bin/sh\nexit 0\n")
        self.wine.chmod(0o755)
        runtime_add(self.root, "test", self.wine, self.wine, [], "server")

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, **kwargs):
        return game_add(self.root, definition(), self.source, "test", **kwargs)

    def test_manifest_paths_cannot_escape(self):
        for exe in [
            "../Game.exe",
            "/Game.exe",
            "C:\\Game.exe",
            "a/../../Game.exe",
            "a\0.exe",
        ]:
            with self.subTest(exe=exe), self.assertRaises(ConfigError):
                manifest(dict(definition(), executable=exe))

    def test_manifest_does_not_accept_secrets_or_runtime_overrides(self):
        for key in [
            "API_TOKEN",
            "password",
            "WINEPREFIX",
            "DYLD_INSERT_LIBRARIES",
            "PATH",
            "WINEMSYNC",
        ]:
            with self.subTest(key=key), self.assertRaises(ConfigError):
                manifest(definition(environment={key: "value"}))

    def test_arguments_are_literal(self):
        args = ["space here", "$(touch unwanted)", "; exit", "`id`", "中文"]
        self.assertEqual(manifest(definition(arguments=args))["arguments"], args)
        with self.assertRaises(ConfigError):
            manifest(definition(arguments="Game.exe && command"))

    def test_reject_unknown_and_duplicate_fields(self):
        with self.assertRaises(ConfigError):
            manifest(definition(password="bad"))
        f = self.base / "bad.json"
        f.write_text('{"id":"a","id":"b"}')
        with self.assertRaises(ConfigError):
            load_json(f)

    def test_json_limit(self):
        f = self.base / "large.json"
        f.write_text('{"x":"' + "a" * 1024 * 1024 + '"}')
        with self.assertRaises(ConfigError):
            load_json(f)

    def test_pe_architecture_and_invalid_header(self):
        self.assertEqual(pe_architecture(self.source / "Game.exe"), "x86")
        pe(self.source / "x64.exe", 0x8664)
        self.assertEqual(pe_architecture(self.source / "x64.exe"), "x64")
        (self.source / "bad.exe").write_text("not PE")
        with self.assertRaises(ConfigError):
            pe_architecture(self.source / "bad.exe")

    def test_prefix_and_steam_client_are_not_game_sources(self):
        (self.source / "system.reg").touch()
        (self.source / "dosdevices").mkdir()
        with self.assertRaises(ConfigError):
            validate_source(self.source)
        (self.source / "system.reg").unlink()
        (self.source / "steam.exe").touch()
        (self.source / "config").mkdir()
        with self.assertRaises(ConfigError):
            inventory(self.source)

    def test_external_symlink_is_rejected(self):
        (self.source / "escape").symlink_to(self.base)
        with self.assertRaises(ConfigError):
            self.register()

    def test_copy_is_isolated_and_source_settings_unchanged(self):
        p = self.register()
        copy = Path(p["game_path"])
        (copy / "settings.cfg").write_text("changed")
        self.assertEqual((self.source / "settings.cfg").read_text(), "original")
        self.assertEqual(
            (self.source / "Game.exe").read_bytes(), (copy / "Game.exe").read_bytes()
        )

    def test_copy_inside_source_refused(self):
        with self.assertRaises(ConfigError):
            clone_game(self.source, self.source / "copy")

    def test_registration_conflict_does_not_leave_game_copy(self):
        self.register(adopt=True)
        second = dict(definition(), id="second")
        rt = self.root / "runtimes/test.json"
        data = load_json(rt)
        data["sync"] = "esync"
        write_private(rt, data)
        with self.assertRaises(ConfigError):
            game_add(
                self.root,
                second,
                self.source,
                "test",
                prefix=self.root / "sessions/sample/Prefix",
            )
        self.assertFalse((self.root / "games/second").exists())

    def test_failed_profile_write_rolls_back_import(self):
        from wgfm.core import write_private as real_write

        def write(path, data):
            if path.parent.name == "profiles":
                raise OSError("simulated disk failure")
            real_write(path, data)

        with (
            patch("wgfm.core.write_private", side_effect=write),
            self.assertRaises(OSError),
        ):
            self.register()
        self.assertFalse((self.root / "games/sample/Game").exists())
        self.assertEqual(list((self.root / "sessions").glob("*.json")), [])

    def test_profile_permissions_are_private(self):
        self.register()
        self.assertEqual(
            stat.S_IMODE((self.root / "profiles/sample.json").stat().st_mode), 0o600
        )
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)

    def test_same_prefix_uses_exclusive_lock(self):
        p = self.register()
        with (
            session_lock(self.root, p),
            self.assertRaises(ConfigError),
            session_lock(self.root, p),
        ):
            pass
        with session_lock(self.root, p):
            pass

    def test_steam_needs_matching_probe(self):
        with self.assertRaises(ConfigError):
            game_add(self.root, definition(steam_app_id="10"), self.source, "test")
        probe = self.base / "probe.exe"
        pe(probe, 0x8664)
        with self.assertRaises(ConfigError):
            game_add(
                self.root,
                definition(steam_app_id="10"),
                self.source,
                "test",
                steam=self.source / "Game.exe",
                probe=probe,
            )

    def test_doctor_detects_changed_game_binary(self):
        p = self.register()
        Path(p["game_path"], "Game.exe").write_bytes(b"changed")
        self.assertFalse(
            doctor(self.root, "sample")["checks"]["game_executable_unchanged"]
        )

    def test_environment_does_not_inherit_tokens_or_mix_sync(self):
        p = self.register()
        rt = load_json(self.root / "runtimes/test.json")
        rt["sync"] = "msync"
        with patch.dict(
            os.environ, {"GITHUB_TOKEN": "private", "DYLD_INSERT_LIBRARIES": "unwanted"}
        ):
            env = environment(rt, p)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("DYLD_INSERT_LIBRARIES", env)
        self.assertEqual(env["WINEMSYNC"], "1")
        self.assertEqual(env["WINEESYNC"], "0")

    def test_inventory_detects_changed_and_missing_assets(self):
        expected = inventory(self.source)
        (self.source / "Game.exe").unlink()
        (self.source / "settings.cfg").write_text("modified")
        r = compare_inventory(expected, self.source)
        self.assertEqual(r["missing"], ["Game.exe"])
        self.assertEqual(r["different"], ["settings.cfg"])
        self.assertFalse(r["passed"])

    def test_diagnostics_does_not_export_private_paths_or_raw_logs(self):
        self.register()
        (self.root / "logs").mkdir()
        (self.root / "logs/sample.log").write_text("private-account-data")
        out = self.base / "diagnostics.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "--home",
                        str(self.root),
                        "diagnostics",
                        "sample",
                        "--output",
                        str(out),
                    ]
                ),
                0,
            )
        data = out.read_text()
        self.assertNotIn(str(self.base), data)
        self.assertNotIn("private-account-data", data)

    def test_artwork_update_does_not_modify_game_assets(self):
        p = self.register()
        before = inventory(Path(p["game_path"]))
        image = self.base / "cover.png"
        image.write_bytes(b"test")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "--home",
                        str(self.root),
                        "artwork",
                        "sample",
                        "--cover",
                        str(image),
                    ]
                ),
                0,
            )
        self.assertEqual(before, inventory(Path(p["game_path"])))


if __name__ == "__main__":
    unittest.main()
