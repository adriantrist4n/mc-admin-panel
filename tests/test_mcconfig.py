#!/usr/bin/env python3
"""Tests de mcconfig.py. Usan directorios temporales con su propio
server.properties sintético, no dependen del servidor real."""
import os
import sys
import tempfile
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
import mcconfig


def write_properties(directory, content):
    with open(os.path.join(directory, "server.properties"), "w") as f:
        f.write(content)


class EnvVarGuard:
    """Quita unas env vars al entrar y restaura su valor original al salir."""

    def __init__(self, *names):
        self.names = names
        self.saved = {}

    def __enter__(self):
        for name in self.names:
            self.saved[name] = os.environ.pop(name, None)
        return self

    def __exit__(self, *exc):
        for name, val in self.saved.items():
            if val is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = val


class TestReadProperties(unittest.TestCase):
    def test_parses_keys(self):
        with tempfile.TemporaryDirectory() as d:
            write_properties(
                d,
                "level-name=miworld\nenable-rcon=true\n# comentario\n\n"
                "rcon.password=secreto==con==igual\n",
            )
            props = mcconfig.read_properties(d)
        self.assertEqual(props["level-name"], "miworld")
        self.assertEqual(props["enable-rcon"], "true")
        self.assertEqual(props["rcon.password"], "secreto==con==igual")
        self.assertNotIn("# comentario", props)

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mcconfig.read_properties(d), {})


class TestRconCreds(unittest.TestCase):
    def test_reads_from_properties(self):
        with EnvVarGuard("RCON_PORT", "RCON_PASSWORD"), tempfile.TemporaryDirectory() as d:
            write_properties(d, "enable-rcon=true\nrcon.port=25599\nrcon.password=hunter2\n")
            host, port, password, enabled = mcconfig.rcon_creds(d)
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 25599)
        self.assertEqual(password, "hunter2")
        self.assertTrue(enabled)

    def test_defaults_when_missing(self):
        with EnvVarGuard("RCON_PORT", "RCON_PASSWORD"), tempfile.TemporaryDirectory() as d:
            _, port, password, enabled = mcconfig.rcon_creds(d)
        self.assertEqual(port, 25575)
        self.assertEqual(password, "")
        self.assertFalse(enabled)

    def test_env_overrides_properties(self):
        with EnvVarGuard("RCON_PORT", "RCON_PASSWORD"), tempfile.TemporaryDirectory() as d:
            write_properties(d, "rcon.port=25599\nrcon.password=fromfile\n")
            os.environ["RCON_PORT"] = "9999"
            os.environ["RCON_PASSWORD"] = "fromenv"
            _, port, password, _ = mcconfig.rcon_creds(d)
        self.assertEqual(port, 9999)
        self.assertEqual(password, "fromenv")


class TestWorldDir(unittest.TestCase):
    def test_custom_level_name(self):
        with tempfile.TemporaryDirectory() as d:
            write_properties(d, "level-name=miworld\n")
            self.assertEqual(mcconfig.world_dir(d), os.path.join(d, "miworld"))

    def test_defaults_to_world(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mcconfig.world_dir(d), os.path.join(d, "world"))


class TestServerName(unittest.TestCase):
    def test_env_override(self):
        with EnvVarGuard("MC_ADMIN_SERVER_NAME"), tempfile.TemporaryDirectory() as d:
            os.environ["MC_ADMIN_SERVER_NAME"] = "MiServidor"
            self.assertEqual(mcconfig.server_name(d), "MiServidor")

    def test_falls_back_to_folder_name(self):
        with EnvVarGuard("MC_ADMIN_SERVER_NAME"), tempfile.TemporaryDirectory() as d:
            self.assertEqual(mcconfig.server_name(d), os.path.basename(d))


class TestRuntimeDir(unittest.TestCase):
    def test_stable_and_exists(self):
        with EnvVarGuard("MC_ADMIN_RUNTIME_DIR"), tempfile.TemporaryDirectory() as d:
            rt1 = mcconfig.runtime_dir(d)
            rt2 = mcconfig.runtime_dir(d)
            self.assertEqual(rt1, rt2)
            self.assertTrue(os.path.isdir(rt1))

    def test_different_dirs_differ(self):
        with EnvVarGuard("MC_ADMIN_RUNTIME_DIR"):
            with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
                self.assertNotEqual(mcconfig.runtime_dir(d1), mcconfig.runtime_dir(d2))

    def test_env_override_wins(self):
        with EnvVarGuard("MC_ADMIN_RUNTIME_DIR"), tempfile.TemporaryDirectory() as override_dir:
            os.environ["MC_ADMIN_RUNTIME_DIR"] = override_dir
            self.assertEqual(mcconfig.runtime_dir("/cualquier/cosa"), override_dir)


if __name__ == "__main__":
    unittest.main()
