#!/usr/bin/env python3
"""Núcleo multiplataforma del toolkit: ciclo de vida del servidor (arrancar,
detener, reiniciar) y backups del mundo.

Es el equivalente en Python de la lógica que antes vivía en bash (lib.sh +
admin.sh + backup.sh), para que funcione igual en Linux y en Windows. Lo usan:
  - dashboard.py (en proceso, importándolo) para start/stop/restart/backup,
  - admin.sh / backup.sh en Linux (delegan vía la CLI de abajo),
  - admin.bat / backup.bat en Windows (ídem).

CLI:
    python mcadmin.py --do-start | --do-stop | --do-restart | --running | --backup

Cada función de ciclo de vida devuelve (codigo, mensaje); la CLI imprime el
mensaje y sale con ese código (0 ok · 1 error · 2 no-op/ya-en-marcha).
"""
import glob
import os
import shlex
import socket
import subprocess
import sys
import tarfile
import time

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SERVER_DIR)
import mcconfig
import rcon as rcon_mod

try:
    import psutil
except ImportError:  # backup no necesita psutil; el ciclo de vida sí
    psutil = None

IS_WINDOWS = os.name == "nt"

JAR_CANDIDATES = ["fabric-server-launch.jar", "server.jar", "paper.jar",
                  "purpur.jar", "spigot.jar"]


# ---------------------------------------------------------------- helpers

def _runtime_paths(server_dir):
    rt = mcconfig.runtime_dir(server_dir)
    return (os.path.join(rt, "server.pid"),
            os.path.join(rt, "console.log"),
            os.path.join(rt, "idle-monitor.pid"))


def _rcon(server_dir, command):
    host, port, password, enabled = mcconfig.rcon_creds(server_dir)
    if not enabled:
        return None
    try:
        return rcon_mod.rcon_command(host, port, password, command)
    except Exception:
        return None


def detect_jar(server_dir):
    """Adivina el jar del servidor; None si es ambiguo (varios .jar)."""
    for candidate in JAR_CANDIDATES:
        if os.path.isfile(os.path.join(server_dir, candidate)):
            return candidate
    jars = [os.path.basename(p) for p in glob.glob(os.path.join(server_dir, "*.jar"))]
    return jars[0] if len(jars) == 1 else None


def resolve_launch_cmd(server_dir, cfg=None):
    """Lista de argumentos para arrancar el servidor (o None si no se puede).

    Usa SERVER_START_CMD si está definido; si no, 'java <JVM_ARGS> -jar <jar>
    nogui' con SERVER_JAR o autodetección.
    """
    cfg = cfg if cfg is not None else mcconfig.load_config(server_dir)
    start_cmd = cfg.get("SERVER_START_CMD", "").strip()
    if start_cmd:
        return shlex.split(start_cmd, posix=not IS_WINDOWS)
    jar = cfg.get("SERVER_JAR", "").strip() or detect_jar(server_dir)
    if not jar or not os.path.isfile(os.path.join(server_dir, jar)):
        return None
    args = ["java"] + shlex.split(cfg.get("JVM_ARGS", ""), posix=not IS_WINDOWS)
    args += ["-jar", jar, "nogui"]
    return args


def find_java_pid(server_dir):
    """PID de un proceso java cuyo cwd sea exactamente server_dir, o None.
    Reconoce un servidor ya en marcha sin depender del nombre del jar."""
    if psutil is None:
        return None
    target = os.path.abspath(server_dir)
    for p in psutil.process_iter(["name"]):
        try:
            name = (p.info["name"] or "").lower()
            if not name.startswith("java"):  # java / java.exe / javaw.exe
                continue
            if os.path.abspath(p.cwd()) == target:
                return p.pid
        except (psutil.Error, OSError, TypeError):
            continue
    return None


def _read_pid(pid_file):
    try:
        with open(pid_file) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid):
    if pid is None:
        return False
    if psutil is not None:
        return psutil.pid_exists(pid)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def server_running(server_dir):
    """PID del servidor si está en marcha, si no None. Adopta por cwd un java
    ya en ejecución cuando el PID file no existe o quedó obsoleto."""
    pid_file, _, _ = _runtime_paths(server_dir)
    pid = _read_pid(pid_file)
    if _pid_alive(pid):
        return pid
    if os.path.exists(pid_file):
        try:
            os.remove(pid_file)
        except OSError:
            pass
    pid = find_java_pid(server_dir)
    if pid is not None:
        try:
            with open(pid_file, "w") as f:
                f.write(str(pid))
        except OSError:
            pass
        return pid
    return None


def _is_listening(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _spawn_detached(args, server_dir, stdout):
    """Lanza un proceso que sobreviva al panel, multiplataforma."""
    kwargs = dict(cwd=server_dir, stdout=stdout, stderr=subprocess.STDOUT,
                  stdin=subprocess.DEVNULL, close_fds=True)
    if IS_WINDOWS:
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                   | getattr(subprocess, "DETACHED_PROCESS", 0))
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


def _terminate(pid):
    if pid is None:
        return
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except psutil.TimeoutExpired:
                proc.kill()
        except psutil.Error:
            pass
        return
    try:  # sin psutil (poco habitual): mejor esfuerzo
        os.kill(pid, 15)
        time.sleep(2)
        os.kill(pid, 9)
    except OSError:
        pass


# ------------------------------------------------------------- ciclo de vida

def do_start(server_dir=SERVER_DIR):
    pid = server_running(server_dir)
    if pid is not None:
        return 2, f"El servidor ya está en ejecución (PID {pid})."

    cfg = mcconfig.load_config(server_dir)
    cmd = resolve_launch_cmd(server_dir, cfg)
    if not cmd:
        return 1, ("No se pudo determinar cómo arrancar el servidor. "
                   "Define SERVER_JAR o SERVER_START_CMD en config.sh.")

    pid_file, console_log, idle_pid_file = _runtime_paths(server_dir)
    host, rcon_port, _pw, rcon_enabled = mcconfig.rcon_creds(server_dir)

    try:
        log = open(console_log, "wb")
    except OSError as exc:
        return 1, f"No se pudo abrir el log de consola: {exc}"
    try:
        proc = _spawn_detached(cmd, server_dir, log)
    except OSError as exc:
        log.close()
        return 1, f"No se pudo arrancar el servidor: {exc}"
    finally:
        log.close()

    try:
        with open(pid_file, "w") as f:
            f.write(str(proc.pid))
    except OSError:
        pass

    # Puerto que indica "listo": el de RCON si está habilitado, si no el de juego.
    if rcon_enabled:
        check_port = rcon_port
    else:
        props = mcconfig.read_properties(server_dir)
        try:
            check_port = int(props.get("server-port", "25565"))
        except ValueError:
            check_port = 25565

    deadline = time.time() + 60
    while time.time() < deadline:
        time.sleep(2)
        if proc.poll() is not None and not server_running(server_dir):
            return 1, f"El servidor terminó al arrancar. Revisa {console_log}"
        if _is_listening(check_port):
            time.sleep(2)
            if rcon_enabled and cfg.get("IDLE_ENABLED", "true").lower() == "true":
                _start_idle_monitor(server_dir, cfg, idle_pid_file)
            return 0, f"Servidor iniciado correctamente (PID {proc.pid})."
    return 1, f"El servidor no respondió en 60s. Revisa {console_log}"


def _start_idle_monitor(server_dir, cfg, idle_pid_file):
    env = os.environ.copy()
    env.setdefault("IDLE_TIMEOUT", str(cfg.get("IDLE_TIMEOUT", "300")))
    env.setdefault("IDLE_CHECK_INTERVAL", str(cfg.get("IDLE_CHECK_INTERVAL", "30")))
    try:
        kwargs = dict(cwd=server_dir, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                      env=env, close_fds=True)
        if IS_WINDOWS:
            kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                       | getattr(subprocess, "DETACHED_PROCESS", 0))
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            [sys.executable, os.path.join(server_dir, "idle-monitor.py")], **kwargs)
        with open(idle_pid_file, "w") as f:
            f.write(str(proc.pid))
    except OSError:
        pass


def do_stop(server_dir=SERVER_DIR):
    pid = server_running(server_dir)
    if pid is None:
        return 2, "El servidor no está en ejecución."

    pid_file, _, idle_pid_file = _runtime_paths(server_dir)
    _, _, _pw, rcon_enabled = mcconfig.rcon_creds(server_dir)
    if rcon_enabled:
        _rcon(server_dir, "say §cEl servidor se detendrá en 5 segundos...")
        time.sleep(2)
        _rcon(server_dir, "stop")
        time.sleep(5)

    if _pid_alive(pid):
        _terminate(pid)

    for f in (pid_file,):
        try:
            os.remove(f)
        except OSError:
            pass
    idle_pid = _read_pid(idle_pid_file)
    if idle_pid is not None:
        _terminate(idle_pid)
        try:
            os.remove(idle_pid_file)
        except OSError:
            pass
    return 0, "Servidor detenido."


def do_restart(server_dir=SERVER_DIR):
    if server_running(server_dir) is not None:
        _, _, _pw, rcon_enabled = mcconfig.rcon_creds(server_dir)
        if rcon_enabled:
            _rcon(server_dir, "say §cReiniciando servidor...")
            time.sleep(1)
    _, stop_msg = do_stop(server_dir)
    rc, start_msg = do_start(server_dir)
    return rc, f"{stop_msg}\n{start_msg}"


# ----------------------------------------------------------------- backup

def _backup_log(server_dir, msg):
    backup_dir = os.path.join(server_dir, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(os.path.join(backup_dir, "backup.log"), "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {msg}\n")
    except OSError:
        pass


def backup(server_dir=SERVER_DIR):
    cfg = mcconfig.load_config(server_dir)
    backup_dir = os.path.join(server_dir, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    world_dir = mcconfig.world_dir(server_dir)
    world_name = os.path.basename(world_dir.rstrip(os.sep))
    if not os.path.isdir(world_dir):
        _backup_log(server_dir, f"ERROR: no existe el mundo {world_dir}")
        return 1, f"No se encuentra el mundo: {world_dir}"

    _backup_log(server_dir, "Iniciando backup...")
    _, _, _pw, rcon_enabled = mcconfig.rcon_creds(server_dir)
    if rcon_enabled:
        _rcon(server_dir, "save-off")
        _rcon(server_dir, "save-all")
        time.sleep(2)
        _rcon(server_dir, "save-off")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(backup_dir, f"world-{stamp}.tar.gz")
    ok = True
    try:
        with tarfile.open(backup_file, "w:gz") as tar:
            tar.add(world_dir, arcname=world_name)
    except (OSError, tarfile.TarError) as exc:
        ok = False
        _backup_log(server_dir, f"ERROR: fallo al comprimir: {exc}")

    if rcon_enabled:
        _rcon(server_dir, "save-on")

    if not ok or not os.path.isfile(backup_file):
        try:
            os.remove(backup_file)
        except OSError:
            pass
        return 1, "El backup falló. Revisa backups/backup.log"

    size = os.path.getsize(backup_file)
    _backup_log(server_dir, f"Backup completado: {backup_file} ({size} bytes)")
    _prune_backups(server_dir, cfg)
    _backup_log(server_dir, "---")
    return 0, f"Backup completado: {os.path.basename(backup_file)}"


def _prune_backups(server_dir, cfg):
    try:
        max_backups = int(cfg.get("MAX_BACKUPS", "14"))
    except ValueError:
        max_backups = 14
    backup_dir = os.path.join(server_dir, "backups")
    files = sorted(glob.glob(os.path.join(backup_dir, "world-*.tar.gz")),
                   key=os.path.getmtime, reverse=True)
    for old in files[max_backups:]:
        try:
            os.remove(old)
            _backup_log(server_dir, f"Backup antiguo eliminado: {old}")
        except OSError:
            pass


# -------------------------------------------------------------------- CLI

def main(argv):
    actions = {
        "--do-start": do_start,
        "--do-stop": do_stop,
        "--do-restart": do_restart,
        "--backup": backup,
    }
    if not argv:
        print("Uso: mcadmin.py --do-start|--do-stop|--do-restart|--running|--backup")
        return 1
    arg = argv[0]
    if arg == "--running":
        return 0 if server_running(SERVER_DIR) is not None else 1
    fn = actions.get(arg)
    if fn is None:
        print(f"Acción desconocida: {arg}")
        return 1
    rc, msg = fn(SERVER_DIR)
    print(msg)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
