#!/usr/bin/env python3
"""
Dashboard de administración en vivo para un servidor Minecraft
(vanilla/Paper/Purpur/Spigot/Fabric) en Linux.

Lo lanza admin.sh con el intérprete del venv (.venv-admin). Muestra en tiempo
real CPU/RAM/heap de la JVM, GC, TPS/MSPT, jugadores, disco, etc., y aloja
también el menú de control (comando RCON, consola, logs, backup, start/stop).

Modo de prueba (sin UI, imprime un snapshot y sale):
    .venv-admin/bin/python dashboard.py --probe
"""

import os
import re
import sys
import time
import json
import shutil
import select
import termios
import tty
import threading
import subprocess
from collections import deque

import psutil

from rich.console import Console, Group
from rich.layout import Layout
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich.table import Table
from rich.align import Align
from rich import box

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SERVER_DIR)
import rcon as rcon_mod  # reutiliza la lógica RCON probada (rcon.py)
import mcconfig

RUNTIME_DIR = mcconfig.runtime_dir(SERVER_DIR)
PID_FILE = os.path.join(RUNTIME_DIR, "server.pid")
CONSOLE_LOG = os.path.join(RUNTIME_DIR, "console.log")
IDLE_PID_FILE = os.path.join(RUNTIME_DIR, "idle-monitor.pid")
WORLD_DIR = mcconfig.world_dir(SERVER_DIR)
ADMIN_SH = os.path.join(SERVER_DIR, "admin.sh")

NCPU = psutil.cpu_count() or 1
SPARKS = "▁▂▃▄▅▆▇█"


# ============================================================ configuración

def server_meta():
    """Nombre, MOTD, máximo de jugadores, puerto y versión del servidor."""
    meta = {"name": mcconfig.server_name(SERVER_DIR), "motd": "Servidor Minecraft",
            "max_players": None, "port": "25565", "version": "?"}
    try:
        with open(os.path.join(SERVER_DIR, "server.properties")) as f:
            for line in f:
                line = line.strip()
                if line.startswith("motd="):
                    meta["motd"] = line.split("=", 1)[1] or meta["motd"]
                elif line.startswith("max-players="):
                    meta["max_players"] = line.split("=", 1)[1]
                elif line.startswith("server-port="):
                    meta["port"] = line.split("=", 1)[1]
    except OSError:
        pass
    try:
        vdir = os.path.join(SERVER_DIR, "versions")
        subs = sorted(d for d in os.listdir(vdir)
                      if os.path.isdir(os.path.join(vdir, d)))
        if subs:
            meta["version"] = subs[-1]
    except OSError:
        pass
    return meta


# ================================================================= parsers

def _to_mb(num, unit):
    n = float(num)
    return n / 1024 if unit == "K" else n * 1024 if unit == "G" else n


def parse_heap_info(text):
    """jcmd GC.heap_info -> (used_mb, capacity_mb, max_mb) o None."""
    if not text:
        return None
    # ZGC: "ZHeap used 1022M, capacity 2048M, max capacity 8192M"
    m = re.search(r"used\s+(\d+(?:\.\d+)?)([KMG]).*?capacity\s+(\d+(?:\.\d+)?)([KMG])"
                  r".*?max capacity\s+(\d+(?:\.\d+)?)([KMG])", text, re.S)
    if m:
        return (_to_mb(m.group(1), m.group(2)),
                _to_mb(m.group(3), m.group(4)),
                _to_mb(m.group(5), m.group(6)))
    # G1/genérico: "total 2097152K, used 1048576K"
    m = re.search(r"total\s+(\d+(?:\.\d+)?)([KMG]),\s*used\s+(\d+(?:\.\d+)?)([KMG])", text)
    if m:
        cap = _to_mb(m.group(1), m.group(2))
        used = _to_mb(m.group(3), m.group(4))
        return (used, cap, cap)
    return None


def parse_jstat(text):
    """jstat -gc -> dict con contadores GC (YGC/FGC/CGC) y GCT (segundos)."""
    if not text:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    header = lines[0].split()
    values = lines[1].replace(",", ".").split()
    raw = dict(zip(header, values))
    out = {}
    for key in ("YGC", "FGC", "CGC", "GCT"):
        val = raw.get(key)
        if val in (None, "-"):
            out[key] = None
            continue
        try:
            out[key] = float(val) if key == "GCT" else int(float(val))
        except ValueError:
            out[key] = None
    return out


def parse_tick(text):
    """RCON 'tick query' -> dict con target/mspt/percentiles/tps."""
    if not text:
        return None
    out = {}
    m = re.search(r"Target tick rate:\s*([\d.]+)", text)
    out["target"] = float(m.group(1)) if m else 20.0
    m = re.search(r"Average time per tick:\s*([\d.]+)\s*ms", text)
    out["mspt"] = float(m.group(1)) if m else None
    for p in ("P50", "P95", "P99"):
        m = re.search(p + r":\s*([\d.]+)\s*ms", text)
        out[p.lower()] = float(m.group(1)) if m else None
    if out["mspt"] and out["mspt"] > 0:
        out["tps"] = min(out["target"], 1000.0 / out["mspt"])
    else:
        out["tps"] = out["target"]
    return out


def parse_list(text):
    """RCON 'list' -> dict con online/max/nombres."""
    if not text:
        return None
    m = re.search(r"There are (\d+) of a max of (\d+) players online:?\s*(.*)",
                  text, re.S)
    if not m:
        return None
    names = [n.strip() for n in m.group(3).split(",") if n.strip()]
    return {"online": int(m.group(1)), "max": int(m.group(2)), "names": names}


def parse_difficulty(text):
    m = re.search(r"difficulty is (\w+)", text or "")
    return m.group(1) if m else None


def parse_gametime(text):
    m = re.search(r"game time is (\d+)", text or "")
    return int(m.group(1)) if m else None


def parse_daytime(text):
    """'... minecraft:day is at 1251 tick(s)' -> 1251 (tick dentro del día 0-24000)."""
    m = re.search(r"at (\d+) tick", text or "")
    return int(m.group(1)) if m else None


def parse_forceload(text):
    if not text:
        return None
    if "No force loaded" in text:
        return 0
    return len(re.findall(r"\[-?\d+,\s*-?\d+\]", text)) or None


def parse_worldborder(text):
    m = re.search(r"currently (\d+(?:\.\d+)?) block", text or "")
    return float(m.group(1)) if m else None


def ticks_to_clock(daytime):
    """daytime 0-24000 (0 = 06:00) -> ('HH:MM', es_de_dia)."""
    if daytime is None:
        return ("—", True)
    t = daytime % 24000
    hours = (t / 1000 + 6) % 24
    h = int(hours)
    return (f"{h:02d}:{int((hours - h) * 60):02d}", 0 <= t < 12000)


def entity_scalar(text):
    """'... entity data: 20.0f' -> 20.0"""
    m = re.search(r"entity data:\s*(-?\d+(?:\.\d+)?)", text or "")
    return float(m.group(1)) if m else None


def entity_str(text):
    m = re.search(r'entity data:\s*"?([^"\n]+?)"?\s*$', text or "")
    return m.group(1).replace("minecraft:", "") if m else None


def entity_pos(text):
    if not text:
        return None
    nums = re.findall(r"-?\d+(?:\.\d+)?", text.split("entity data:", 1)[-1])
    if len(nums) >= 3:
        return f"({float(nums[0]):.0f}, {float(nums[1]):.0f}, {float(nums[2]):.0f})"
    return None


def read_temp():
    """Temperatura de CPU (°C) de los sensores disponibles, o None."""
    try:
        temps = psutil.sensors_temperatures()
    except Exception:
        return None
    if not temps:
        return None
    for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
        if temps.get(key):
            return max(t.current for t in temps[key] if t.current is not None)
    allt = [t.current for v in temps.values() for t in v if t.current is not None]
    return max(allt) if allt else None


# ============================================================ colector (hilo)

class Monitor(threading.Thread):
    """Recoge métricas en segundo plano y mantiene el último snapshot."""

    HIST = 60

    def __init__(self):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.host, self.port, self.password, self.rcon_enabled = mcconfig.rcon_creds(SERVER_DIR)
        self.meta = server_meta()
        self.proc = None
        self.proc_pid = None
        self._tick = 0
        self._last_io = None          # (bytes_acumulados, ts)
        self._last_net = None         # (rx, tx, ts)
        self._du = None               # (tamaño_bytes, ts)
        self._heap = None
        self._gc = None
        self._tick_data = None
        self._players = None
        self._last_mc_ok = 0.0
        self._world = None            # info de mundo (cadencia lenta)
        self._players_detail = {}     # detalle por jugador
        self._idle = None             # estado del idle-monitor
        self._events = deque(maxlen=10)  # feed de actividad
        self._log_pos = None          # ((dev, ino), offset) para el tail incremental
        self.state = self._blank()
        self.hist = {k: deque(maxlen=self.HIST)
                     for k in ("cpu", "ram", "heap", "mspt", "net")}
        psutil.cpu_percent(interval=None)        # cebado sistema (global)
        psutil.cpu_percent(percpu=True)          # cebado sistema (por núcleo)
        try:
            psutil.net_io_counters()             # cebado de red
        except Exception:
            pass

    def _blank(self):
        st = {"status": "OFFLINE", "pid": None, "uptime": 0, "mc_ok": False}
        st["meta"] = self.meta
        return st

    def stop(self):
        self._stop.set()

    def snapshot(self):
        with self._lock:
            snap = dict(self.state)
            snap["hist"] = {k: list(v) for k, v in self.hist.items()}
        return snap

    def rcon(self, cmd):
        try:
            return rcon_mod.rcon_command(self.host, self.port, self.password, cmd)
        except Exception:
            return None

    def _run(self, args, timeout=5, lc_c=False):
        try:
            env = dict(os.environ)
            if lc_c:
                env["LC_ALL"] = "C"
            return subprocess.run(args, capture_output=True, text=True,
                                  timeout=timeout, env=env).stdout
        except (subprocess.SubprocessError, OSError):
            return None

    def _world_size(self):
        try:
            r = subprocess.run(["du", "-sb", WORLD_DIR], capture_output=True,
                               text=True, timeout=25)
            return int(r.stdout.split()[0])
        except (subprocess.SubprocessError, OSError, ValueError, IndexError):
            return None

    def _player_detail(self, names):
        out = {}
        for name in names[:6]:
            lvl = entity_scalar(self.rcon(f"data get entity {name} XpLevel"))
            out[name] = {
                "health": entity_scalar(self.rcon(f"data get entity {name} Health")),
                "lvl": int(lvl) if lvl is not None else None,
                "dim": entity_str(self.rcon(f"data get entity {name} Dimension")),
                "pos": entity_pos(self.rcon(f"data get entity {name} Pos")),
            }
        return out

    def _read_events(self):
        """Tail incremental de logs/latest.log -> self._events (filtrando spam RCON)."""
        path = os.path.join(SERVER_DIR, "logs", "latest.log")
        try:
            stt = os.stat(path)
        except OSError:
            return
        inode = (stt.st_dev, stt.st_ino)
        if self._log_pos is None:
            # primer enganche: empezar cerca del final (no volcar el log entero)
            self._log_pos = (inode, max(0, stt.st_size - 6000))
        elif self._log_pos[0] != inode or stt.st_size < self._log_pos[1]:
            self._log_pos = (inode, 0)  # rotado o truncado
        try:
            with open(path, "r", errors="replace") as f:
                f.seek(self._log_pos[1])
                data = f.read()
                self._log_pos = (inode, f.tell())
        except OSError:
            return
        for line in data.splitlines():
            ev = self._parse_event(line)
            if ev:
                self._events.append(ev)

    @staticmethod
    def _parse_event(line):
        """(ts, mensaje, estilo) para eventos de interés, o None para descartar."""
        m = re.match(r"\[(\d\d:\d\d:\d\d)\] \[([^\]]+)\]:\s?(.*)", line)
        if not m:
            return None
        ts, thread, msg = m.group(1), m.group(2), m.group(3).strip()
        if "RCON" in thread or not msg:
            return None
        if msg.startswith("[Not Secure] "):   # chat moderno: quita el prefijo de ruido
            msg = msg[13:]
        if "/ERROR" in thread or "/FATAL" in thread:
            return (ts, msg, "red")
        if "/WARN" in thread:
            return (ts, msg, "yellow")
        if "joined the game" in msg:
            return (ts, msg, "green")
        if "left the game" in msg:
            return (ts, msg, "grey50")
        if re.match(r"<[^>]+>", msg) or msg.startswith(("[Rcon]", "[Server]")):
            return (ts, msg, "cyan")
        if any(k in msg for k in ("made the advancement", "completed the challenge",
                                  "reached the goal")):
            return (ts, msg, "magenta")
        if re.search(r"(was slain|was shot|was killed|was blown|was pricked|was burn|"
                     r"drowned|fell|blew up|hit the ground|tried to swim|suffocat|"
                     r"withered|starved|froze|burned to death| died)", msg):
            return (ts, msg, "bright_red")
        if any(k in msg for k in ("Starting minecraft", "Stopping", "Done (",
                                  "Preparing spawn", "Saving", "Loaded ")):
            return (ts, msg, "white")
        return None

    def _read_idle(self):
        """Estado del idle-monitor: {'alive': bool, 'mode': 'IDLE'|'NORMAL'|None}."""
        alive = False
        try:
            with open(IDLE_PID_FILE) as f:
                alive = psutil.pid_exists(int(f.read().strip()))
        except (OSError, ValueError):
            pass
        mode = None
        try:
            with open(os.path.join(SERVER_DIR, "logs", "idle-monitor.log"), "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 4000))
                tail = f.read().decode("utf-8", "replace")
            for line in reversed(tail.splitlines()):
                if "IDLE MODE ACTIVATED" in line:
                    mode = "IDLE"
                    break
                if "NORMAL MODE RESTORED" in line:
                    mode = "NORMAL"
                    break
        except OSError:
            pass
        return {"alive": alive, "mode": mode}

    def _find_proc(self):
        pid = None
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            if not psutil.pid_exists(pid):
                pid = None
        except (OSError, ValueError):
            pid = None
        if pid is None:
            # Genérico (vale para vanilla/Paper/Purpur/Spigot/Fabric, cualquier
            # jar): un proceso java cuyo directorio de trabajo es este servidor.
            for p in psutil.process_iter(["name"]):
                try:
                    if p.info["name"] != "java":
                        continue
                    if p.cwd() == SERVER_DIR:
                        pid = p.pid
                        break
                except (psutil.Error, TypeError, OSError):
                    continue
        if pid is None:
            self.proc = self.proc_pid = None
            return None
        if self.proc is None or self.proc_pid != pid:
            try:
                self.proc = psutil.Process(pid)
                self.proc_pid = pid
                self.proc.cpu_percent(interval=None)  # cebar el % del proceso
            except psutil.Error:
                self.proc = self.proc_pid = None
        return self.proc

    def run(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._collect()
            except Exception as exc:  # el colector nunca debe morir
                with self._lock:
                    self.state["error"] = str(exc)
            self._tick += 1
            self._stop.wait(max(0.25, 1.0 - (time.time() - t0)))

    def _collect(self):
        proc = self._find_proc()
        if proc is None:
            with self._lock:
                self.state = self._blank()
            return

        st = {"meta": self.meta, "pid": proc.pid, "ts": time.time()}

        # --- métricas del SO (cada tick) ---
        try:
            with proc.oneshot():
                st["cpu"] = proc.cpu_percent(interval=None) / NCPU
                st["rss"] = proc.memory_info().rss
                st["threads"] = proc.num_threads()
                try:
                    st["fds"] = proc.num_fds()
                except (psutil.Error, AttributeError):
                    st["fds"] = None
                st["uptime"] = time.time() - proc.create_time()
                try:
                    io = proc.io_counters()
                    total, now = io.read_bytes + io.write_bytes, time.time()
                    if self._last_io:
                        dt = now - self._last_io[1]
                        st["io_rate"] = max(0.0, (total - self._last_io[0]) / dt) if dt > 0 else 0.0
                    self._last_io = (total, now)
                except (psutil.Error, AttributeError):
                    st["io_rate"] = None
        except psutil.Error:
            pass
        vm = psutil.virtual_memory()
        st["mem_total"], st["mem_pct"] = vm.total, vm.percent
        st["cpu_sys"] = psutil.cpu_percent(interval=None)
        st["per_core"] = psutil.cpu_percent(percpu=True)
        try:
            st["load"] = os.getloadavg()
        except OSError:
            st["load"] = None
        try:
            net = psutil.net_io_counters()
            now = time.time()
            if self._last_net:
                dt = now - self._last_net[2]
                if dt > 0:
                    st["net_rx"] = max(0.0, (net.bytes_recv - self._last_net[0]) / dt)
                    st["net_tx"] = max(0.0, (net.bytes_sent - self._last_net[1]) / dt)
            self._last_net = (net.bytes_recv, net.bytes_sent, now)
        except Exception:
            pass
        try:
            sw = psutil.swap_memory()
            st["swap"] = (sw.used, sw.total, sw.percent)
        except Exception:
            st["swap"] = None
        st["temp"] = read_temp()
        self._read_events()
        self._idle = self._read_idle()
        st["idle"] = self._idle
        st["events"] = list(self._events)

        # --- JVM (cada 2 ticks, par) ---
        if self._tick % 2 == 0:
            self._heap = parse_heap_info(
                self._run(["jcmd", str(proc.pid), "GC.heap_info"]))
            self._gc = parse_jstat(
                self._run(["jstat", "-gc", str(proc.pid)], lc_c=True))
        st["heap"], st["gc"] = self._heap, self._gc
        st["rcon_enabled"] = self.rcon_enabled

        if self.rcon_enabled:
            # --- RCON (cada 2 ticks, impar) ---
            if self._tick % 2 == 1:
                td = parse_tick(self.rcon("tick query"))
                pl = parse_list(self.rcon("list"))
                if td is not None:
                    self._tick_data = td
                if pl is not None:
                    self._players = pl
                if td is not None or pl is not None:
                    self._last_mc_ok = time.time()
        st["tick"], st["players"] = self._tick_data, self._players
        st["mc_ok"] = (time.time() - self._last_mc_ok) < 8
        st["status"] = "STARTING" if (not st["mc_ok"] and st["uptime"] < 60) else "ONLINE"

        if self.rcon_enabled:
            # --- info del mundo (RCON, lento ~10s) ---
            if self._tick % 10 == 5:
                w = dict(self._world or {})
                for key, val in (("daytime", parse_daytime(self.rcon("time query day"))),
                                 ("gametime", parse_gametime(self.rcon("time query gametime"))),
                                 ("difficulty", parse_difficulty(self.rcon("difficulty"))),
                                 ("forceload", parse_forceload(self.rcon("forceload query"))),
                                 ("border", parse_worldborder(self.rcon("worldborder get")))):
                    if val is not None:
                        w[key] = val
                self._world = w
        st["world"] = self._world

        # --- detalle de jugadores (RCON, ~6s, solo si hay alguien) ---
        names = (self._players or {}).get("names") or []
        if not names:
            self._players_detail = {}
        elif self.rcon_enabled and self._tick % 6 == 4:
            self._players_detail = self._player_detail(names)
        st["players_detail"] = self._players_detail

        # --- disco (cada 60s) ---
        now = time.time()
        if self._du is None or now - self._du[1] > 60:
            size = self._world_size()
            if size is not None:
                self._du = (size, now)
        st["world_size"] = self._du[0] if self._du else None
        try:
            st["disk_free"] = shutil.disk_usage(SERVER_DIR).free
        except OSError:
            st["disk_free"] = None

        with self._lock:
            self.state = st
            if st.get("cpu") is not None:
                self.hist["cpu"].append(st["cpu"])
            if st.get("rss") and st.get("mem_total"):
                self.hist["ram"].append(st["rss"] / st["mem_total"] * 100)
            if st.get("heap"):
                used, _cap, mx = st["heap"]
                self.hist["heap"].append(used / mx * 100 if mx else 0)
            if st.get("tick") and st["tick"].get("mspt") is not None:
                self.hist["mspt"].append(st["tick"]["mspt"])
            if st.get("net_rx") is not None:
                self.hist["net"].append(st["net_rx"] + (st.get("net_tx") or 0))


# ============================================================ helpers de UI

def human(n, per_sec=False):
    """Formatea bytes -> 1.2G, 340M, ..."""
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024:
            s = f"{n:.0f}{unit}" if unit in ("B", "K") else f"{n:.1f}{unit}"
            return s + ("/s" if per_sec else "")
        n /= 1024
    return f"{n:.1f}P" + ("/s" if per_sec else "")


def color_for(pct, lo=60, hi=85, reverse=False):
    pct = pct or 0
    if reverse:
        return "green" if pct >= hi else "yellow" if pct >= lo else "red"
    return "green" if pct < lo else "yellow" if pct < hi else "red"


def bar(pct, width=18, lo=60, hi=85, reverse=False):
    pct = max(0.0, min(100.0, pct or 0))
    filled = int(round(width * pct / 100))
    color = color_for(pct, lo, hi, reverse)
    t = Text()
    t.append("█" * filled, style=color)
    t.append("░" * (width - filled), style="grey30")
    return t


def dim_bar(width=18):
    return Text("░" * width, style="grey30")


def spark(values, color="cyan"):
    if not values:
        return Text("")
    vals = list(values)[-24:]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    t = Text()
    for v in vals:
        idx = int((v - lo) / rng * (len(SPARKS) - 1))
        t.append(SPARKS[max(0, min(len(SPARKS) - 1, idx))])
    t.stylize(color)
    return t


def core_strip(per_core):
    """Una tira de glifos, un núcleo por glifo, coloreado por carga."""
    if not per_core:
        return Text("—", style="grey30")
    t = Text()
    for load in per_core:
        load = max(0.0, min(100.0, load))
        idx = int(load / 100 * (len(SPARKS) - 1))
        t.append(SPARKS[idx], style=color_for(load))
    return t


def temp_color(temp):
    if temp is None:
        return "white"
    return "green" if temp < 60 else "yellow" if temp < 78 else "red"


def fmt_uptime(secs):
    secs = int(secs or 0)
    d, h, m = secs // 86400, (secs % 86400) // 3600, (secs % 3600) // 60
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def fmt_ms(x):
    return f"{x:.1f}" if x is not None else "—"


def mspt_color(mspt):
    if mspt is None:
        return "white"
    return "green" if mspt < 30 else "yellow" if mspt < 50 else "red"


def fmt_gc(gc):
    if not gc:
        return "—"
    parts = []
    if gc.get("YGC") is not None:
        parts.append(f"{gc['YGC']}y")
    if gc.get("CGC") is not None:
        parts.append(f"{gc['CGC']}c")
    if gc.get("FGC"):
        parts.append(f"{gc['FGC']}f")
    out = " ".join(parts) if parts else "?"
    if gc.get("GCT") is not None:
        out += f" · {gc['GCT'] * 1000:.0f}ms"
    return out


# ================================================================== paneles

def header_panel(snap):
    meta = snap["meta"]
    status = snap.get("status", "OFFLINE")
    badge = {"ONLINE": "green", "STARTING": "yellow", "OFFLINE": "red"}[status]
    emoji = {"ONLINE": "🟢", "STARTING": "🟡", "OFFLINE": "🔴"}[status]

    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right")

    left = Text()
    left.append(f"⚡ {meta.get('name', 'SERVIDOR MINECRAFT')}\n", style="bold magenta")
    left.append(meta.get("motd", ""), style="dim italic")

    right = Text()
    right.append(f" {emoji} {status} ", style=f"bold white on {badge}")
    right.append("\n")
    bits = []
    if snap.get("pid"):
        bits.append(f"PID {snap['pid']}")
    bits.append(f"v{meta.get('version', '?')}")
    bits.append(f":{meta.get('port', '?')}")
    if snap.get("uptime"):
        bits.append("up " + fmt_uptime(snap["uptime"]))
    right.append("   ".join(bits), style="cyan")

    grid.add_row(left, right)
    return Panel(grid, box=box.HEAVY, border_style=badge, padding=(0, 1))


def resources_panel(snap):
    hist = snap.get("hist", {})
    g = Table.grid(expand=True, padding=(0, 1))
    g.add_column(width=5, style="bold")
    g.add_column(width=18)
    g.add_column(justify="right", width=13)
    g.add_column(ratio=1, no_wrap=True)

    # CPU
    cpu = snap.get("cpu")
    if cpu is not None:
        g.add_row("CPU", bar(cpu), f"{cpu:.0f}%", spark(hist.get("cpu"), "cyan"))
    else:
        g.add_row("CPU", dim_bar(), "—", Text())

    # RAM del proceso
    rss, mt = snap.get("rss"), snap.get("mem_total")
    if rss and mt:
        g.add_row("RAM", bar(rss / mt * 100), f"{human(rss)}/{human(mt)}",
                  spark(hist.get("ram"), "blue"))
    else:
        g.add_row("RAM", dim_bar(), "—", Text())

    # Heap JVM
    heap = snap.get("heap")
    if heap:
        used, _cap, mx = heap
        pct = used / mx * 100 if mx else 0
        g.add_row("Heap", bar(pct), f"{used / 1024:.1f}/{mx / 1024:.1f}G",
                  spark(hist.get("heap"), "magenta"))
    else:
        g.add_row("Heap", dim_bar(), "—", Text())

    extra = Text()
    pc = snap.get("per_core")
    if pc:
        extra.append("Núcleos ", style="bold cyan")
        extra.append_text(core_strip(pc))
        extra.append(f"  {sum(pc) / len(pc):.0f}%\n")
    extra.append("GC ", style="bold cyan")
    extra.append(fmt_gc(snap.get("gc")) + "\n")
    extra.append("Hilos ", style="bold cyan")
    extra.append(f"{snap.get('threads', '—')}    ")
    extra.append("FDs ", style="bold cyan")
    extra.append(f"{snap.get('fds', '—')}    ")
    extra.append("IO ", style="bold cyan")
    extra.append(human(snap.get("io_rate"), per_sec=True) + "\n")
    extra.append("Red ", style="bold cyan")
    rx, tx = snap.get("net_rx"), snap.get("net_tx")
    extra.append(f"↓{human(rx, per_sec=True)} ↑{human(tx, per_sec=True)}    "
                 if rx is not None else "—    ")
    temp = snap.get("temp")
    extra.append("Temp ", style="bold cyan")
    extra.append(f"{temp:.0f}°C" if temp is not None else "—", style=temp_color(temp))
    extra.append("    ")
    sw = snap.get("swap")
    extra.append("Swap ", style="bold cyan")
    extra.append((f"{human(sw[0])}/{human(sw[1])} {sw[2]:.0f}%" if sw else "—") + "\n")
    extra.append("Mundo ", style="bold cyan")
    extra.append(human(snap.get("world_size")) + "    ")
    extra.append("Disco libre ", style="bold cyan")
    extra.append(human(snap.get("disk_free")))
    load = snap.get("load")
    if load:
        extra.append("\nload ", style="bold cyan")
        extra.append(f"{load[0]:.2f}  {load[1]:.2f}  {load[2]:.2f}")

    return Panel(Group(g, Text(), extra), title="[bold]📊 Recursos[/]",
                 title_align="left", box=box.ROUNDED, border_style="cyan",
                 padding=(1, 2))


def minecraft_panel(snap):
    g = Table.grid(expand=True, padding=(0, 1))
    g.add_column(width=6, style="bold")
    g.add_column(ratio=1, no_wrap=True)

    if snap.get("rcon_enabled") is False:
        g.add_row("⚠", Text("RCON desactivado", style="bold yellow"))
        g.add_row("", Text("Activa enable-rcon=true + rcon.password", style="dim"))
        g.add_row("", Text("en server.properties para ver TPS/jugadores.", style="dim"))
        g.add_row("", Text())

    tick = snap.get("tick")
    if tick:
        tps = tick.get("tps") or 0
        target = tick.get("target", 20) or 20
        line = Text()
        line.append(f"{tps:4.1f}  ", style=f"bold {color_for(tps / target * 100, 80, 95, reverse=True)}")
        line.append_text(bar(tps / target * 100, width=12, lo=80, hi=95, reverse=True))
        g.add_row("TPS", line)
        mspt = tick.get("mspt")
        g.add_row("MSPT", Text(f"{fmt_ms(mspt)} ms", style=f"bold {mspt_color(mspt)}"))
        pct = Text()
        pct.append(f"P50 {fmt_ms(tick.get('p50'))}   ", style="dim")
        pct.append(f"P95 {fmt_ms(tick.get('p95'))}   ", style="dim")
        pct.append(f"P99 {fmt_ms(tick.get('p99'))}", style="dim")
        g.add_row("", pct)
    else:
        g.add_row("TPS", Text("—", style="dim"))

    world = snap.get("world") or {}
    if world:
        gt = world.get("gametime")
        clock, is_day = ticks_to_clock(world.get("daytime"))
        wline = Text()
        if gt is not None:
            wline.append(f"Día {gt // 24000}   ", style="white")
        wline.append(("☀ " if is_day else "🌙 ") + clock,
                     style="yellow" if is_day else "blue")
        g.add_row("", Text())
        g.add_row("Mundo", wline)
        idle = snap.get("idle") or {}
        ahorro = idle.get("mode") == "IDLE"
        dl = Text()
        dl.append(f"{world.get('difficulty', '—')}", style="white")
        dl.append("   💤 ", style="dim")
        dl.append("Ahorro ON" if ahorro else "Ahorro —",
                  style="bold yellow" if ahorro else "dim")
        g.add_row("", dl)
        fl = world.get("forceload")
        wb = world.get("border")
        wbs = f"{wb / 1e6:.0f}M" if wb else "—"
        g.add_row("", Text(f"Chunks {fl if fl is not None else '—'}   Borde {wbs}",
                           style="dim"))

    g.add_row("", Text())

    players = snap.get("players")
    if players:
        n, mx = players["online"], players["max"]
        g.add_row("Jug.", Text(f"{n} / {mx}",
                               style=f"bold {'green' if n > 0 else 'grey50'}"))
        names = players.get("names") or []
        if names:
            detail = snap.get("players_detail") or {}
            for name in names[:6]:
                d = detail.get(name) or {}
                line = Text()
                line.append("• " + name, style="bold green")
                if d.get("health") is not None:
                    line.append(f"  ❤{d['health']:.0f}", style="red")
                if d.get("lvl") is not None:
                    line.append(f"  ⬆{d['lvl']}", style="green")
                if d.get("dim"):
                    line.append(f"  {d['dim']}", style="cyan")
                if d.get("pos"):
                    line.append(f"  {d['pos']}", style="grey50")
                g.add_row("", line)
        else:
            g.add_row("", Text("(servidor vacío)", style="dim italic"))
    else:
        maxp = snap["meta"].get("max_players") or "?"
        g.add_row("Jug.", Text(f"— / {maxp}", style="dim"))
        if snap.get("status") == "STARTING":
            g.add_row("", Text("esperando RCON…", style="yellow"))

    return Panel(g, title="[bold]🎮 Minecraft[/]", title_align="left",
                 box=box.ROUNDED, border_style="green", padding=(1, 2))


def offline_panel(snap):
    t = Text()
    t.append("🔴  SERVIDOR DETENIDO\n\n", style="bold red")
    t.append("El servidor de Minecraft no está en ejecución.\n\n", style="white")
    t.append("Pulsa ", style="dim")
    t.append("[q]", style="bold yellow")
    t.append(" para abrir el menú y arrancarlo.", style="dim")
    return Panel(Align.center(t, vertical="middle"), box=box.ROUNDED,
                 border_style="red")


def footer():
    t = Text()
    t.append("↻ 1s", style="dim")
    t.append("    ·    ", style="grey30")
    t.append("[q]", style="bold yellow")
    t.append(" menú", style="dim")
    t.append("    ·    ", style="grey30")
    t.append("[Q / Ctrl-C]", style="bold yellow")
    t.append(" salir", style="dim")
    return Align.center(t)


def activity_panel(snap):
    events = snap.get("events") or []
    if not events:
        body = Align.center(Text("(sin actividad reciente)", style="dim italic"))
    else:
        body = Text(no_wrap=True, overflow="ellipsis")
        for i, (ts, msg, style) in enumerate(events[-10:]):
            if i:
                body.append("\n")
            body.append(f"{ts} ", style="grey42")
            body.append(msg, style=style or "white")
    return Panel(body, title="[bold]📜 Actividad reciente[/]", title_align="left",
                 box=box.ROUNDED, border_style="blue", padding=(0, 1))


def make_layout(snap):
    layout = Layout()
    if snap.get("status", "OFFLINE") == "OFFLINE":
        layout.split_column(
            Layout(name="header", size=4),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=1),
        )
        layout["header"].update(header_panel(snap))
        layout["body"].update(offline_panel(snap))
        layout["footer"].update(footer())
        return layout

    layout.split_column(
        Layout(name="header", size=4),
        Layout(name="middle", ratio=2),
        Layout(name="activity", ratio=1, minimum_size=6),
        Layout(name="footer", size=1),
    )
    layout["header"].update(header_panel(snap))
    layout["middle"].split_row(Layout(name="left", ratio=3),
                               Layout(name="right", ratio=2))
    layout["middle"]["left"].update(resources_panel(snap))
    layout["middle"]["right"].update(minecraft_panel(snap))
    layout["activity"].update(activity_panel(snap))
    layout["footer"].update(footer())
    return layout


# ================================================================== teclado

def setup_kb():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    return old


def restore_kb(old):
    try:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)
    except Exception:
        pass


# Buffer de bytes pendientes. Imprescindible leer del fd crudo con os.read (sin el
# buffer de sys.stdin): si no, una lectura se traga toda la secuencia de la flecha
# (\x1b[A) y el siguiente select no ve los bytes restantes -> se interpretaría ESC.
_key_buf = b""


def read_key_token(timeout):
    """Lee una pulsación del teclado y devuelve un token.

    'UP'/'DOWN'/'LEFT'/'RIGHT', 'ENTER', 'ESC', 'CTRLC', 'BACKSPACE', un carácter, o None.
    """
    global _key_buf
    fd = sys.stdin.fileno()
    if not _key_buf:
        try:
            r, _, _ = select.select([fd], [], [], timeout)
        except (OSError, ValueError):
            return None
        if not r:
            return None
        try:
            chunk = os.read(fd, 64)
        except OSError:
            return None
        if not chunk:
            return None
        _key_buf = chunk

    b = _key_buf
    first = b[:1]
    if first in (b"\r", b"\n"):
        _key_buf = b[1:]
        return "ENTER"
    if first == b"\x03":
        _key_buf = b[1:]
        return "CTRLC"
    if first == b"\x7f":
        _key_buf = b[1:]
        return "BACKSPACE"
    if first == b"\x1b":
        # secuencia de flecha: ESC [ <letra>  (o ESC O <letra> en modo aplicación)
        if b[1:2] in (b"[", b"O") and len(b) >= 3:
            code = b[2:3]
            _key_buf = b[3:]
            return {b"A": "UP", b"B": "DOWN", b"C": "RIGHT", b"D": "LEFT"}.get(code, "ESC")
        if len(b) == 1:  # quizá venía partida (SSH lento): intenta completar
            try:
                r2, _, _ = select.select([fd], [], [], 0.05)
                if r2:
                    _key_buf += os.read(fd, 8)
                    b = _key_buf
                    if b[1:2] in (b"[", b"O") and len(b) >= 3:
                        code = b[2:3]
                        _key_buf = b[3:]
                        return {b"A": "UP", b"B": "DOWN", b"C": "RIGHT", b"D": "LEFT"}.get(code, "ESC")
            except OSError:
                pass
        _key_buf = b[1:]
        return "ESC"
    _key_buf = b[1:]
    try:
        return first.decode("utf-8", "ignore") or None
    except Exception:
        return None


# =============================================================== menú (rich)

def is_running(mon):
    return bool(mon.snapshot().get("pid"))


def latest_backup():
    d = os.path.join(SERVER_DIR, "backups")
    try:
        files = [os.path.join(d, f) for f in os.listdir(d)
                 if f.startswith("world-") and f.endswith(".tar.gz")]
    except OSError:
        return None
    return max(files, key=os.path.getmtime) if files else None


def info_box(msg, color="cyan", title=""):
    return Panel(Text(msg, style="white"),
                 title=(f"[bold]{title}[/]" if title else None),
                 border_style=color, box=box.ROUNDED, padding=(1, 2))


def screen_with(console, mon, body, hint="Pulsa una tecla para volver"):
    """Pinta cabecera + cuerpo + pista en la pantalla normal (con Live detenido)."""
    console.clear()
    console.print(header_panel(mon.snapshot()))
    console.print(body)
    console.print(Align.center(Text(hint, style="dim")))


def pause_key(timeout=120):
    read_key_token(timeout)


def build_menu_options(snap):
    """Lista contextual de opciones: (clave, icono, etiqueta, descripción)."""
    online = bool(snap.get("pid")) and snap.get("status") != "OFFLINE"
    opts = [
        ("command", "💬", "Enviar comando RCON", "Ejecuta un comando en la consola del servidor"),
        ("console", "⌨️", "Consola en vivo", "Sigue la salida del servidor en directo (tail -f)"),
        ("logs", "📜", "Ver últimos logs", "Muestra las últimas líneas de logs/latest.log"),
        ("backup", "💾", "Backup del mundo", "Crea ahora un .tar.gz del mundo"),
    ]
    if online:
        opts.append(("restart", "🔄", "Reiniciar servidor", "Detiene y vuelve a arrancar el servidor"))
        opts.append(("stop", "🔴", "Detener servidor", "Apaga el servidor de forma segura"))
    else:
        opts.append(("start", "🟢", "Iniciar servidor", "Arranca el servidor de Minecraft"))
    opts.append(("back", "📊", "Volver al dashboard", "Vuelve a la vista en vivo"))
    opts.append(("quit", "🚪", "Salir del panel", "Cierra el panel de administración"))
    return opts


def menu_options_panel(options, sel):
    g = Table.grid(expand=True, padding=(0, 1))
    g.add_column(width=2, justify="right")
    g.add_column(width=3)
    g.add_column(ratio=1)
    for i, (key, icon, label, _desc) in enumerate(options):
        if i == sel:
            g.add_row(Text("▶", style="bold cyan"),
                      Text(icon, style="black on cyan"),
                      Text(f" {label} ", style="bold black on cyan"))
        else:
            g.add_row(Text(str(i + 1), style="grey42"),
                      Text(icon),
                      Text(label, style="white"))
    return Panel(g, title="[bold]🎮 Menú de control[/]", title_align="left",
                 subtitle=f"[dim italic]{options[sel][3]}[/]", subtitle_align="left",
                 box=box.ROUNDED, border_style="cyan", padding=(1, 2))


def mini_stats_panel(snap):
    g = Table.grid(expand=True, padding=(0, 1))
    g.add_column(width=9, style="bold cyan")
    g.add_column(ratio=1)
    status = snap.get("status", "OFFLINE")
    badge = {"ONLINE": "green", "STARTING": "yellow", "OFFLINE": "red"}.get(status, "red")
    g.add_row("Estado", Text(status, style=f"bold {badge}"))
    if snap.get("pid"):
        g.add_row("PID", Text(str(snap["pid"]), style="white"))
    if snap.get("uptime"):
        g.add_row("Uptime", Text(fmt_uptime(snap["uptime"]), style="white"))
    cpu = snap.get("cpu")
    g.add_row("CPU", Text(f"{cpu:.0f}%" if cpu is not None else "—", style="white"))
    rss, mt = snap.get("rss"), snap.get("mem_total")
    g.add_row("RAM", Text(f"{human(rss)}/{human(mt)}" if rss and mt else "—", style="white"))
    heap = snap.get("heap")
    if heap:
        g.add_row("Heap", Text(f"{heap[0] / 1024:.1f}/{heap[2] / 1024:.1f}G", style="white"))
    tick = snap.get("tick")
    if tick:
        g.add_row("TPS", Text(f"{tick.get('tps', 0):.1f}", style="white"))
    pl = snap.get("players")
    if pl:
        g.add_row("Jugadores", Text(f"{pl['online']}/{pl['max']}", style="white"))
    temp = snap.get("temp")
    if temp is not None:
        g.add_row("Temp", Text(f"{temp:.0f}°C", style=temp_color(temp)))
    return Panel(g, title="[bold]📈 Estado en vivo[/]", title_align="left",
                 box=box.ROUNDED, border_style="green", padding=(1, 2))


def menu_footer():
    t = Text()
    t.append("↑↓", style="bold yellow")
    t.append(" mover", style="dim")
    t.append("    ·    ", style="grey30")
    t.append("Enter/nº", style="bold yellow")
    t.append(" elegir", style="dim")
    t.append("    ·    ", style="grey30")
    t.append("q/Esc", style="bold yellow")
    t.append(" volver al dashboard", style="dim")
    return Align.center(t)


def menu_layout(snap, options, sel):
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=4),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=1),
    )
    layout["header"].update(header_panel(snap))
    layout["body"].split_row(Layout(name="menu", ratio=3), Layout(name="info", ratio=2))
    layout["body"]["menu"].update(menu_options_panel(options, sel))
    layout["body"]["info"].update(mini_stats_panel(snap))
    layout["footer"].update(menu_footer())
    return layout


def confirm_screen(console, mon, title, question, danger=True):
    sel = 1 if danger else 0           # por defecto "No" en acciones peligrosas
    while True:
        opts = Text(justify="center")
        for i, lab in enumerate(("Sí", "No")):
            opts.append(f"  {lab}  ", style="bold black on cyan" if i == sel else "white")
            opts.append("    ")
        body = Panel(Align.center(Group(Align.center(Text(question)), Text(""), Align.center(opts))),
                     title=f"[bold]{title}[/]", border_style="yellow", box=box.ROUNDED,
                     padding=(1, 2))
        console.clear()
        console.print(header_panel(mon.snapshot()))
        console.print(body)
        console.print(Align.center(Text("←→ elegir · Enter confirmar · Esc cancela", style="dim")))
        k = read_key_token(60)
        if k in ("LEFT", "RIGHT"):
            sel ^= 1
        elif k in ("y", "Y", "s", "S"):
            return True
        elif k in ("n", "N", "ESC"):
            return False
        elif k == "ENTER":
            return sel == 0


def submenu_command(console, mon, kb_old):
    snap = mon.snapshot()
    if not snap.get("pid"):
        screen_with(console, mon, info_box("El servidor no está en ejecución.", "red",
                                           "💬 Enviar comando"))
        pause_key()
        return
    if snap.get("rcon_enabled") is False:
        screen_with(console, mon, info_box(
            "RCON está desactivado en server.properties.\n"
            "Activa enable-rcon=true y fija rcon.password, luego reinicia.",
            "red", "💬 Enviar comando"))
        pause_key()
        return
    console.clear()
    console.print(header_panel(mon.snapshot()))
    console.print(Panel(Text("Escribe un comando RCON (sin la '/'). Vacío = volver.",
                             style="dim"),
                        title="[bold]💬 Enviar comando[/]", title_align="left",
                        border_style="cyan", box=box.ROUNDED, padding=(1, 2)))
    if kb_old is not None:
        restore_kb(kb_old)                       # modo cooked para editar la línea
    try:
        cmd = console.input("[bold cyan]» [/]").strip()
    except (EOFError, KeyboardInterrupt):
        cmd = ""
    if sys.stdin.isatty():
        tty.setcbreak(sys.stdin.fileno())        # volver a cbreak
    if not cmd:
        return
    result = mon.rcon(cmd)
    screen_with(console, mon,
                Panel(Text(result or "(sin respuesta)", style="white"),
                      title=f"[bold]» {cmd}[/]", title_align="left",
                      border_style="green", box=box.ROUNDED, padding=(1, 2)))
    pause_key()


def submenu_console(console, mon):
    if not is_running(mon):
        screen_with(console, mon, info_box("El servidor no está en ejecución.", "red",
                                           "⌨️ Consola"))
        pause_key()
        return
    console.clear()
    console.print(Align.center(Text("⌨️  Consola en vivo — Ctrl-C para volver al menú\n",
                                    style="bold cyan")))
    try:
        subprocess.run(["tail", "-f", CONSOLE_LOG])
    except (KeyboardInterrupt, OSError, subprocess.SubprocessError):
        pass


def submenu_logs(console, mon):
    path = os.path.join(SERVER_DIR, "logs", "latest.log")
    try:
        with open(path, errors="replace") as f:
            raw = f.readlines()[-400:]
    except OSError:
        raw = []
    # filtra el ruido de conexiones RCON (lo genera el propio polling del panel)
    lines = [ln for ln in raw
             if "RCON Listener" not in ln and "RCON Client" not in ln][-22:]
    if not lines:
        body = Panel(Align.center(Text("(sin logs)", style="dim italic")),
                     title="[bold]📜 Últimos logs[/]", border_style="blue", box=box.ROUNDED)
    else:
        txt = Text(no_wrap=True, overflow="ellipsis")
        for ln in lines:
            ln = ln.rstrip("\n")
            ev = Monitor._parse_event(ln)
            txt.append(ln + "\n", style=(ev[2] if ev else "grey50"))
        body = Panel(txt, title="[bold]📜 Últimos logs[/]", title_align="left",
                     border_style="blue", box=box.ROUNDED, padding=(0, 1))
    screen_with(console, mon, body)
    pause_key()


def submenu_backup(console, mon):
    if not confirm_screen(console, mon, "💾 Backup del mundo",
                          "¿Crear un backup del mundo ahora?", danger=False):
        return
    console.clear()
    console.print(header_panel(mon.snapshot()))
    rc, err = 1, ""
    with console.status("[cyan]Creando backup del mundo…[/]", spinner="dots"):
        try:
            rc = subprocess.run([os.path.join(SERVER_DIR, "backup.sh")],
                                capture_output=True, timeout=600).returncode
        except Exception as exc:
            rc, err = 1, str(exc)
    if rc == 0:
        last = latest_backup()
        if last:
            msg = f"Backup completado:\n{os.path.basename(last)} ({human(os.path.getsize(last))})"
        else:
            msg = "Backup completado."
        body = info_box(msg, "green", "💾 Backup")
    else:
        body = info_box("El backup falló. Revisa backups/backup.log\n" + err, "red", "💾 Backup")
    screen_with(console, mon, body)
    pause_key()


def lifecycle(console, mon, action):
    cfg = {
        "start":   ("🟢 Iniciar servidor", "¿Arrancar el servidor de Minecraft?",
                    "--do-start", "Arrancando servidor…"),
        "stop":    ("🔴 Detener servidor", "¿Detener el servidor de forma segura?",
                    "--do-stop", "Deteniendo servidor…"),
        "restart": ("🔄 Reiniciar servidor", "¿Reiniciar el servidor ahora?",
                    "--do-restart", "Reiniciando servidor…"),
    }
    title, question, arg, spin = cfg[action]
    if not confirm_screen(console, mon, title, question, danger=(action != "start")):
        return
    console.clear()
    console.print(header_panel(mon.snapshot()))
    rc, out = 1, ""
    with console.status(f"[cyan]{spin}[/]", spinner="dots"):
        try:
            p = subprocess.run([ADMIN_SH, arg], capture_output=True, text=True, timeout=180)
            rc, out = p.returncode, (p.stdout or "").strip()
        except Exception as exc:
            out = str(exc)
    color = "green" if rc == 0 else ("yellow" if rc == 2 else "red")
    screen_with(console, mon, info_box(out or "(sin salida)", color, title))
    pause_key()


def run_action(act, console, mon, live, kb_old):
    if act == "back":
        return "DASHBOARD"
    if act == "quit":
        return "QUIT"
    live.stop()
    result = None
    try:
        if act == "command":
            submenu_command(console, mon, kb_old)
        elif act == "console":
            submenu_console(console, mon)
        elif act == "logs":
            submenu_logs(console, mon)
        elif act == "backup":
            submenu_backup(console, mon)
        elif act in ("start", "stop", "restart"):
            lifecycle(console, mon, act)
            result = "DASHBOARD"
    except (Exception, KeyboardInterrupt) as exc:
        try:
            screen_with(console, mon, info_box(f"Error en la acción: {exc}", "red"))
            pause_key()
        except Exception:
            pass
    live.start(refresh=False)
    return result


def menu_mode(console, mon, is_tty, kb_old):
    sel = 0
    with Live(menu_layout(mon.snapshot(), build_menu_options(mon.snapshot()), sel),
              console=console, screen=True, refresh_per_second=8, auto_refresh=False) as live:
        while True:
            options = build_menu_options(mon.snapshot())
            sel = max(0, min(sel, len(options) - 1))
            live.update(menu_layout(mon.snapshot(), options, sel), refresh=True)
            if not is_tty:
                time.sleep(0.3)
                continue
            key = read_key_token(0.3)
            if key is None:
                continue
            act = None
            if key == "UP":
                sel = (sel - 1) % len(options)
            elif key == "DOWN":
                sel = (sel + 1) % len(options)
            elif key in ("q", "ESC"):
                return "DASHBOARD"
            elif key in ("Q", "CTRLC"):
                return "QUIT"
            elif key == "ENTER":
                act = options[sel][0]
            elif key and key.isdigit():
                i = int(key) - 1
                if 0 <= i < len(options):
                    sel, act = i, options[i][0]
            if act:
                res = run_action(act, console, mon, live, kb_old)
                if res in ("DASHBOARD", "QUIT"):
                    return res


# ===================================================================== main

def live_dashboard(console, mon, is_tty):
    with Live(make_layout(mon.snapshot()), console=console, screen=True,
              refresh_per_second=8, auto_refresh=False) as live:
        while True:
            live.update(make_layout(mon.snapshot()), refresh=True)
            if not is_tty:
                time.sleep(0.5)
                continue
            key = read_key_token(0.25)
            if key in ("q", "m", "ENTER"):
                return "MENU"
            if key in ("Q", "CTRLC"):
                return "QUIT"

def run_ui():
    console = Console()
    mon = Monitor()
    mon.start()
    time.sleep(0.4)  # deja que el primer ciclo recoja datos
    is_tty = sys.stdin.isatty()
    kb_old = setup_kb() if is_tty else None
    try:
        mode = "dashboard"
        while True:
            if mode == "dashboard":
                if live_dashboard(console, mon, is_tty) == "QUIT":
                    break
                mode = "menu"
            else:
                if menu_mode(console, mon, is_tty, kb_old) == "QUIT":
                    break
                mode = "dashboard"
    except KeyboardInterrupt:
        pass
    finally:
        if kb_old is not None:
            restore_kb(kb_old)
        mon.stop()
    return 0


def probe():
    """Recoge ~3.5s de métricas y las imprime como JSON (para depuración)."""
    mon = Monitor()
    mon.start()
    time.sleep(3.5)
    snap = mon.snapshot()
    mon.stop()
    snap.pop("hist", None)
    print(json.dumps(snap, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
    else:
        sys.exit(run_ui())
