#!/usr/bin/env python3
"""Funciones compartidas por dashboard.py e idle-monitor.py.

Equivalente en Python de lib.sh: leen server.properties como única fuente de
verdad (RCON, nombre del mundo) y calculan la carpeta de runtime con la misma
fórmula que usa admin.sh, para que el toolkit funcione dentro de la carpeta
raíz de cualquier servidor Minecraft sin hardcodear nada de esta instalación.
"""
import hashlib
import os
import re
import tempfile


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
    """Nombre a mostrar: MC_ADMIN_SERVER_NAME (lo exporta admin.sh) si está; si
    no, SERVER_NAME de config.sh (para que admin.bat no tenga que exportarlo en
    Windows); y como último recurso el nombre de la carpeta del servidor."""
    name = os.environ.get("MC_ADMIN_SERVER_NAME")
    if name:
        return name
    cfg_name = load_config(server_dir).get("SERVER_NAME", "").strip()
    if cfg_name:
        return cfg_name
    return os.path.basename(os.path.abspath(server_dir))


def runtime_dir(server_dir):
    """Carpeta de runtime namespaced (PID files, etc). Si admin.sh/admin.bat ya
    la calcularon y exportaron vía MC_ADMIN_RUNTIME_DIR, se reusa esa (mismo
    proceso padre); si no (ejecución suelta, p. ej. --probe), se recalcula con
    la misma fórmula que lib.sh:mc_runtime_dir. Usa el directorio temporal del
    sistema (tempfile.gettempdir(): /tmp en Linux, %TEMP% en Windows) para ser
    multiplataforma."""
    env = os.environ.get("MC_ADMIN_RUNTIME_DIR")
    if env:
        os.makedirs(env, exist_ok=True)
        return env
    abspath = os.path.abspath(server_dir)
    h = hashlib.md5(abspath.encode("utf-8")).hexdigest()[:8]
    rt = os.path.join(tempfile.gettempdir(), "mc-admin", f"{os.path.basename(abspath)}-{h}")
    os.makedirs(rt, exist_ok=True)
    return rt


# Valores por defecto de config.sh, para no depender de que exista el archivo.
_CONFIG_DEFAULTS = {
    "SERVER_NAME": "",
    "JVM_RAM": "8G",
    "JVM_ARGS": "-Xms2G -Xmx${JVM_RAM} -XX:+UseZGC -XX:ZUncommitDelay=30",
    "SERVER_JAR": "",
    "SERVER_START_CMD": "",
    "IDLE_ENABLED": "true",
    "IDLE_TIMEOUT": "300",
    "IDLE_CHECK_INTERVAL": "30",
    "MAX_BACKUPS": "14",
}

_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _strip_value(raw):
    """Quita comentarios sueltos y comillas de un valor de config.sh."""
    raw = raw.strip()
    if raw and raw[0] in "\"'":
        quote = raw[0]
        end = raw.find(quote, 1)
        if end != -1:
            return raw[1:end]
        return raw[1:]
    # Sin comillas: corta en un comentario ' #' si lo hubiera.
    hashpos = raw.find(" #")
    if hashpos != -1:
        raw = raw[:hashpos]
    return raw.strip()


def load_config(server_dir):
    """Lee config.sh (asignaciones simples KEY=VALOR) en un dict, sin necesitar
    bash, para que admin.bat/mcadmin funcionen en Windows. Expande ${VAR}/$VAR
    con valores ya parseados y aplica los defaults de _CONFIG_DEFAULTS."""
    cfg = dict(_CONFIG_DEFAULTS)
    path = os.path.join(server_dir, "config.sh")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = _ASSIGN_RE.match(line)
                if not m:
                    continue
                cfg[m.group(1)] = _strip_value(m.group(2))
    except OSError:
        pass

    # Expansión de ${VAR} y $VAR usando las claves ya conocidas (p. ej.
    # JVM_ARGS referencia ${JVM_RAM}).
    def expand(val):
        def repl(match):
            name = match.group(1) or match.group(2)
            return cfg.get(name, os.environ.get(name, ""))
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
                      repl, val)

    for key in list(cfg):
        if isinstance(cfg[key], str):
            cfg[key] = expand(cfg[key])
    return cfg
