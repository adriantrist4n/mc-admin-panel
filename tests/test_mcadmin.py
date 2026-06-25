#!/usr/bin/env python3
"""Tests de mcadmin.py (núcleo multiplataforma): config, detección de jar,
comando de arranque y backup. Todo contra directorios temporales; no arranca
ningún servidor real ni necesita RCON (se prueba con enable-rcon=false)."""
import os
import sys
import tarfile
import tempfile
import time
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
import mcadmin
import mcconfig


def write(path, content=""):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class TestLoadConfig(unittest.TestCase):
    def test_parses_and_expands(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "config.sh"),
                  'SERVER_NAME="Mi Servidor"\n'
                  'JVM_RAM="4G"\n'
                  'JVM_ARGS="-Xms1G -Xmx${JVM_RAM}"\n'
                  'MAX_BACKUPS=7\n'
                  '# comentario\n')
            cfg = mcconfig.load_config(d)
        self.assertEqual(cfg["SERVER_NAME"], "Mi Servidor")
        self.assertEqual(cfg["JVM_ARGS"], "-Xms1G -Xmx4G")
        self.assertEqual(cfg["MAX_BACKUPS"], "7")

    def test_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = mcconfig.load_config(d)
        self.assertEqual(cfg["IDLE_TIMEOUT"], "300")
        self.assertEqual(cfg["MAX_BACKUPS"], "14")
        self.assertEqual(cfg["SERVER_JAR"], "")


class TestDetectJar(unittest.TestCase):
    def test_known_name(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "paper.jar"))
            self.assertEqual(mcadmin.detect_jar(d), "paper.jar")

    def test_single_unknown_jar(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "cualquiera.jar"))
            self.assertEqual(mcadmin.detect_jar(d), "cualquiera.jar")

    def test_ambiguous_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "a-extra.jar"))
            write(os.path.join(d, "b-extra.jar"))
            self.assertIsNone(mcadmin.detect_jar(d))


class TestResolveLaunchCmd(unittest.TestCase):
    def test_start_cmd_override(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = dict(mcconfig._CONFIG_DEFAULTS)
            cfg["SERVER_START_CMD"] = "java -jar custom.jar nogui"
            self.assertEqual(mcadmin.resolve_launch_cmd(d, cfg),
                             ["java", "-jar", "custom.jar", "nogui"])

    def test_builds_java_command(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "server.jar"))
            cfg = dict(mcconfig._CONFIG_DEFAULTS)
            cfg["JVM_ARGS"] = "-Xms1G -Xmx2G"
            cfg["SERVER_START_CMD"] = ""
            self.assertEqual(
                mcadmin.resolve_launch_cmd(d, cfg),
                ["java", "-Xms1G", "-Xmx2G", "-jar", "server.jar", "nogui"])

    def test_none_when_no_jar(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = dict(mcconfig._CONFIG_DEFAULTS)
            cfg["SERVER_START_CMD"] = ""
            self.assertIsNone(mcadmin.resolve_launch_cmd(d, cfg))


class TestServerRunning(unittest.TestCase):
    def test_stale_pid_file_is_cleared(self):
        with EnvRuntime() as d:
            pid_file = os.path.join(mcconfig.runtime_dir(d), "server.pid")
            write(pid_file, "999999999")  # PID que no existe
            # Sin proceso java en este cwd -> None y el pid file se limpia.
            self.assertIsNone(mcadmin.server_running(d))
            self.assertFalse(os.path.exists(pid_file))


class TestBackup(unittest.TestCase):
    def test_creates_archive_without_rcon(self):
        with EnvRuntime() as d:
            write(os.path.join(d, "server.properties"), "enable-rcon=false\nlevel-name=world\n")
            os.makedirs(os.path.join(d, "world"))
            write(os.path.join(d, "world", "level.dat"), "datos")
            rc, msg = mcadmin.backup(d)
            self.assertEqual(rc, 0, msg)
            archives = [f for f in os.listdir(os.path.join(d, "backups"))
                        if f.startswith("world-") and f.endswith(".tar.gz")]
            self.assertEqual(len(archives), 1)
            with tarfile.open(os.path.join(d, "backups", archives[0])) as tar:
                self.assertIn("world/level.dat", [m.name for m in tar.getmembers()])

    def test_fails_when_no_world(self):
        with EnvRuntime() as d:
            write(os.path.join(d, "server.properties"), "enable-rcon=false\n")
            rc, _ = mcadmin.backup(d)
            self.assertEqual(rc, 1)

    def test_prune_keeps_newest(self):
        with tempfile.TemporaryDirectory() as d:
            backup_dir = os.path.join(d, "backups")
            os.makedirs(backup_dir)
            now = time.time()
            for i in range(5):
                p = os.path.join(backup_dir, f"world-2024010{i}_000000.tar.gz")
                write(p, "x")
                os.utime(p, (now + i, now + i))  # i mayor = más reciente
            mcadmin._prune_backups(d, {"MAX_BACKUPS": "2"})
            left = sorted(f for f in os.listdir(backup_dir) if f.endswith(".tar.gz"))
            self.assertEqual(left, ["world-20240103_000000.tar.gz",
                                    "world-20240104_000000.tar.gz"])


class EnvRuntime:
    """Directorio temporal con MC_ADMIN_RUNTIME_DIR apuntando dentro de él, para
    que los PID files no se mezclen con el runtime real de la máquina."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self._saved = os.environ.get("MC_ADMIN_RUNTIME_DIR")
        os.environ["MC_ADMIN_RUNTIME_DIR"] = os.path.join(self.dir, ".rt")
        return self.dir

    def __exit__(self, *exc):
        if self._saved is None:
            os.environ.pop("MC_ADMIN_RUNTIME_DIR", None)
        else:
            os.environ["MC_ADMIN_RUNTIME_DIR"] = self._saved
        self._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
