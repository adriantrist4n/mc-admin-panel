#!/usr/bin/env python3
"""Tests de idle-monitor.py: parsing de 'list' y el helper _int_env. Se
monkeypatchea idle_monitor.rcon para no necesitar un servidor real."""
import importlib.util
import os
import sys
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

_spec = importlib.util.spec_from_file_location(
    "idle_monitor", os.path.join(PROJECT_DIR, "idle-monitor.py")
)
idle_monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(idle_monitor)


class TestGetPlayerCount(unittest.TestCase):
    def setUp(self):
        self._orig_rcon = idle_monitor.rcon

    def tearDown(self):
        idle_monitor.rcon = self._orig_rcon

    def test_parses_standard_format(self):
        idle_monitor.rcon = lambda cmd: "There are 3 of a max of 8 players online: a, b, c"
        self.assertEqual(idle_monitor.get_player_count(), 3)

    def test_parses_alt_format(self):
        idle_monitor.rcon = lambda cmd: "5 players online"
        self.assertEqual(idle_monitor.get_player_count(), 5)

    def test_none_when_rcon_unreachable(self):
        idle_monitor.rcon = lambda cmd: None
        self.assertIsNone(idle_monitor.get_player_count())

    def test_zero_on_unparseable(self):
        idle_monitor.rcon = lambda cmd: "respuesta rara"
        self.assertEqual(idle_monitor.get_player_count(), 0)


class TestIntEnv(unittest.TestCase):
    ENV_KEY = "TEST_MC_ADMIN_INT_ENV"

    def tearDown(self):
        os.environ.pop(self.ENV_KEY, None)

    def test_valid_value(self):
        os.environ[self.ENV_KEY] = "42"
        self.assertEqual(idle_monitor._int_env(self.ENV_KEY, 7), 42)

    def test_missing_uses_default(self):
        os.environ.pop(self.ENV_KEY, None)
        self.assertEqual(idle_monitor._int_env(self.ENV_KEY, 7), 7)

    def test_garbage_uses_default(self):
        os.environ[self.ENV_KEY] = "no-es-un-numero"
        self.assertEqual(idle_monitor._int_env(self.ENV_KEY, 7), 7)


if __name__ == "__main__":
    unittest.main()
