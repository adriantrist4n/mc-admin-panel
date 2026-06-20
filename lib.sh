#!/bin/bash
# Funciones compartidas por admin.sh, start.sh, backup.sh e install-service.sh.
# Permiten que el toolkit funcione dentro de la carpeta raíz de cualquier
# servidor Minecraft (vanilla/Paper/Purpur/Spigot/Fabric) sin hardcodear
# nombres de jar, mundo o credenciales RCON.

# read_prop <archivo> <clave> [default]
# Lee "clave=valor" de un .properties (corta solo en el primer '=').
read_prop() {
    local file="$1" key="$2" default="${3:-}" val=""
    if [ -f "$file" ]; then
        val=$(grep -m1 "^${key}=" "$file" 2>/dev/null | cut -d'=' -f2-)
    fi
    if [ -z "$val" ]; then
        echo "$default"
    else
        echo "$val"
    fi
}

# mc_rcon_creds <dir>
# Define RCON_ENABLED / RCON_PORT / RCON_PASSWORD a partir de server.properties
# (única fuente de verdad: es lo que el propio Minecraft usa).
mc_rcon_creds() {
    local dir="$1"
    local props="$dir/server.properties"
    RCON_ENABLED=$(read_prop "$props" "enable-rcon" "false")
    RCON_PORT=$(read_prop "$props" "rcon.port" "25575")
    RCON_PASSWORD=$(read_prop "$props" "rcon.password" "")
}

# mc_runtime_dir <dir>
# Carpeta de runtime (PID/console.log/etc) namespaced por servidor, para que
# dos instalaciones en la misma máquina no se pisen. Misma fórmula que
# mcconfig.runtime_dir() en Python.
mc_runtime_dir() {
    local dir="$1" abs hash rt
    abs=$(cd "$dir" && pwd)
    hash=$(printf '%s' "$abs" | md5sum | cut -c1-8)
    rt="/tmp/mc-admin/$(basename "$abs")-${hash}"
    mkdir -p "$rt"
    echo "$rt"
}

# mc_world_dir <dir>
# Ruta del mundo según level-name de server.properties (por defecto "world").
mc_world_dir() {
    local dir="$1" level
    level=$(read_prop "$dir/server.properties" "level-name" "world")
    echo "$dir/$level"
}

# mc_server_name <dir>
# Nombre a mostrar: $SERVER_NAME si está definida (config.sh), si no el
# nombre de la carpeta.
mc_server_name() {
    local dir="$1"
    if [ -n "${SERVER_NAME:-}" ]; then
        echo "$SERVER_NAME"
    else
        basename "$dir"
    fi
}

# mc_detect_jar <dir>
# Intenta adivinar el jar del servidor. Devuelve 1 si es ambiguo (el caller
# debe pedir SERVER_JAR explícito en config.sh).
mc_detect_jar() {
    local dir="$1" candidate jars=() f
    for candidate in fabric-server-launch.jar server.jar paper.jar purpur.jar spigot.jar; do
        if [ -f "$dir/$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    for f in "$dir"/*.jar; do
        [ -e "$f" ] && jars+=("$(basename "$f")")
    done
    if [ "${#jars[@]}" -eq 1 ]; then
        echo "${jars[0]}"
        return 0
    fi
    return 1
}

# mc_resolve_launch_cmd <dir>
# Comando completo para arrancar el servidor: SERVER_START_CMD (config.sh) si
# está definido, si no "java $JVM_ARGS -jar <jar> nogui" con SERVER_JAR o
# autodetección. Devuelve 1 si no hay forma de determinarlo.
mc_resolve_launch_cmd() {
    local dir="$1"
    if [ -n "${SERVER_START_CMD:-}" ]; then
        echo "$SERVER_START_CMD"
        return 0
    fi
    local jar="${SERVER_JAR:-}"
    if [ -z "$jar" ]; then
        jar=$(mc_detect_jar "$dir") || return 1
    fi
    [ -f "$dir/$jar" ] || return 1
    echo "java $JVM_ARGS -jar $jar nogui"
}

# mc_find_java_pid <dir>
# Busca un proceso java cuyo cwd sea exactamente <dir> (Linux, vía /proc).
# Sirve para reconocer un servidor ya en marcha sin depender del nombre del jar.
mc_find_java_pid() {
    local dir="$1" abs pid cwd
    abs=$(cd "$dir" && pwd)
    for pid in $(pgrep -u "$(id -u)" -x java 2>/dev/null); do
        cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
        if [ "$cwd" = "$abs" ]; then
            echo "$pid"
            return 0
        fi
    done
    return 1
}
