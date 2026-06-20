#!/usr/bin/env python3
"""Funciones compartidas por dashboard.py e idle-monitor.py.

Equivalente en Python de lib.sh: leen server.properties como única fuente de
verdad (RCON, nombre del mundo) y calculan la carpeta de runtime con la misma
fórmula que usa admin.sh, para que el toolkit funcione dentro de la carpeta
raíz de cualquier servidor Minecraft sin hardcodear nada de esta instalación.
"""
import hashlib
import os


def read_properties(server_dir):
    """Parsea server.properties -> dict {clave: valor}. {} si no existe."""
    path = os.path.join(server_dir, "server.properties")
    props = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                props[key.strip()] = val.strip()
    except OSError:
        pass
    return props


def rcon_creds(server_dir):
    """(host, port, password, enabled), con server.properties como fuente y
    RCON_PORT/RCON_PASSWORD de entorno como override opcional."""
    props = read_properties(server_dir)
    port = os.environ.get("RCON_PORT") or props.get("rcon.port") or "25575"
    password = os.environ.get("RCON_PASSWORD")
    if password is None:
        password = props.get("rcon.password", "")
    enabled = props.get("enable-rcon", "false").strip().lower() == "true"
    try:
        port = int(port)
    except ValueError:
        port = 25575
    return "127.0.0.1", port, password, enabled


def world_dir(server_dir):
    """Ruta del mundo según level-name de server.properties (por defecto 'world')."""
    props = read_properties(server_dir)
    level = props.get("level-name") or "world"
    return os.path.join(server_dir, level)


def server_name(server_dir):
    """Nombre a mostrar: MC_ADMIN_SERVER_NAME (lo exporta admin.sh desde
    config.sh) o, si no está, el nombre de la carpeta del servidor."""
    name = os.environ.get("MC_ADMIN_SERVER_NAME")
    if name:
        return name
    return os.path.basename(os.path.abspath(server_dir))


def runtime_dir(server_dir):
    """Carpeta de runtime namespaced (PID files, etc). Si admin.sh ya la
    calculó y exportó vía MC_ADMIN_RUNTIME_DIR, se reusa esa (mismo proceso
    padre); si no (ejecución suelta, p. ej. --probe), se recalcula con la
    misma fórmula que lib.sh:mc_runtime_dir."""
    env = os.environ.get("MC_ADMIN_RUNTIME_DIR")
    if env:
        os.makedirs(env, exist_ok=True)
        return env
    abspath = os.path.abspath(server_dir)
    h = hashlib.md5(abspath.encode("utf-8")).hexdigest()[:8]
    rt = os.path.join("/tmp", "mc-admin", f"{os.path.basename(abspath)}-{h}")
    os.makedirs(rt, exist_ok=True)
    return rt
