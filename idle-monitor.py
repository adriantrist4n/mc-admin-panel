#!/usr/bin/env python3
"""Modo ahorro: baja dificultad/mobs/clima cuando el servidor lleva un rato
vacío, y los restaura en cuanto entra alguien. Lo lanza admin.sh (solo si
IDLE_ENABLED=true y RCON está habilitado); también se puede ejecutar suelto."""
import os
import sys
import time
import re
import subprocess

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SERVER_DIR)
import mcconfig

LOG_FILE = os.path.join(SERVER_DIR, "logs", "latest.log")


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


HOST, PORT, PASSWORD, RCON_ENABLED = mcconfig.rcon_creds(SERVER_DIR)
IDLE_TIMEOUT = _int_env("IDLE_TIMEOUT", 300)        # 5 minutos por defecto
CHECK_INTERVAL = _int_env("IDLE_CHECK_INTERVAL", 30)  # comprobar cada 30s

def rcon(command):
    script = os.path.join(SERVER_DIR, "rcon.py")
    result = subprocess.run(
        [sys.executable, script, HOST, str(PORT), PASSWORD, command],
        capture_output=True, text=True, timeout=10
    )
    out = result.stdout.strip()
    if out == "ERROR" or result.returncode != 0:
        return None
    return out

def get_player_count():
    result = rcon("list")
    if result is None:
        return None
    m = re.search(r"There are (\d+) of a max", result)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+) players? online", result)
    if m:
        return int(m.group(1))
    return 0

def trigger_gc():
    pid_file = os.path.join(mcconfig.runtime_dir(SERVER_DIR), "server.pid")
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            pid = f.read().strip()
        try:
            subprocess.run(["jcmd", pid, "GC.run()"], capture_output=True, timeout=5)
            log("GC triggered via jcmd")
        except Exception as e:
            log(f"GC trigger failed: {e}")

def set_idle_mode(enabled):
    if enabled:
        rcon("difficulty peaceful")
        rcon("gamerule doMobSpawning false")
        rcon("gamerule doWeatherCycle false")
        rcon("gamerule randomTickSpeed 0")
        trigger_gc()
        log("IDLE MODE ACTIVATED")
    else:
        rcon("difficulty normal")
        rcon("gamerule doMobSpawning true")
        rcon("gamerule doWeatherCycle true")
        rcon("gamerule randomTickSpeed 3")
        log("NORMAL MODE RESTORED")

def log(msg):
    log_path = os.path.join(SERVER_DIR, "logs", "idle-monitor.log")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a") as f:
        f.write(f"[{timestamp}] {msg}\n")
    print(f"[idle-monitor] {msg}")

def main():
    if not RCON_ENABLED:
        log("RCON está desactivado en server.properties; idle-monitor no puede funcionar. Saliendo.")
        return
    log("idle-monitor started")
    idle_start = None
    idle_active = False
    last_player_count = 0

    while True:
        time.sleep(CHECK_INTERVAL)

        count = get_player_count()
        if count is None:
            log("WARNING: Could not reach RCON. Server may be offline.")
            idle_start = None
            idle_active = False
            continue

        if count > 0:
            if idle_active:
                log(f"Player(s) detected ({count}). Restoring normal mode.")
                set_idle_mode(False)
                idle_active = False
            idle_start = None
        else:
            if idle_start is None:
                idle_start = time.time()
            elif not idle_active and (time.time() - idle_start) >= IDLE_TIMEOUT:
                log("Server empty for 5 minutes. Activating idle mode (low resources).")
                set_idle_mode(True)
                idle_active = True

        if count != last_player_count:
            log(f"Player count: {count}")

        last_player_count = count

if __name__ == "__main__":
    main()
