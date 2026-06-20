#!/bin/bash
# Tests de lib.sh. No dependen del servidor real: usan directorios temporales
# con su propio server.properties/jars sintéticos.
#
# Uso: ./tests/test_lib.sh   (o vía tests/run_tests.sh)
DIR="$(cd "$(dirname "$0")/.." && pwd)"
source "$DIR/lib.sh"

PASS=0
FAIL=0

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        echo "  FAIL - $desc"
        echo "      esperado: [$expected]"
        echo "      obtenido: [$actual]"
    fi
}

assert_fails() {
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then
        FAIL=$((FAIL + 1))
        echo "  FAIL - $desc (se esperaba que fallara y no falló)"
    else
        PASS=$((PASS + 1))
    fi
}

section() { echo; echo "== $1 =="; }

cleanup() { rm -rf "$T1" "$T2" "$T3" "$T4" "$T5"; }
trap cleanup EXIT

T1=$(mktemp -d); T2=$(mktemp -d); T3=$(mktemp -d); T4=$(mktemp -d); T5=$(mktemp -d)

# ---------------------------------------------------------------- read_prop
section "read_prop"
cat > "$T1/server.properties" <<'EOF'
# comentario, no debe interferir
level-name=miworld
enable-rcon=true
rcon.port=25599
rcon.password=secreto==con==igual
empty-value=
EOF

assert_eq "lee un valor normal" "miworld" "$(read_prop "$T1/server.properties" level-name world)"
assert_eq "usa el default si la clave no existe" "DEF" "$(read_prop "$T1/server.properties" no-existe DEF)"
assert_eq "usa el default si el valor está vacío" "DEF" "$(read_prop "$T1/server.properties" empty-value DEF)"
assert_eq "corta solo en el primer '=' (valores con '=' dentro)" "secreto==con==igual" "$(read_prop "$T1/server.properties" rcon.password "")"
assert_eq "default si el archivo no existe" "DEF" "$(read_prop "$T1/no-existe.properties" level-name DEF)"

# -------------------------------------------------------------- mc_world_dir
section "mc_world_dir"
assert_eq "usa level-name del properties" "$T1/miworld" "$(mc_world_dir "$T1")"
assert_eq "'world' por defecto si no hay server.properties" "$T2/world" "$(mc_world_dir "$T2")"

# ------------------------------------------------------------- mc_rcon_creds
# (regresión del bug real: 'local a=$1 b="$a/x"' en una sola línea no veía $a)
section "mc_rcon_creds"
mc_rcon_creds "$T1"
assert_eq "RCON_ENABLED correcto" "true" "$RCON_ENABLED"
assert_eq "RCON_PORT correcto" "25599" "$RCON_PORT"
assert_eq "RCON_PASSWORD correcto (con '=' dentro)" "secreto==con==igual" "$RCON_PASSWORD"

cat > "$T2/server.properties" <<'EOF'
enable-rcon=false
EOF
mc_rcon_creds "$T2"
assert_eq "RCON_ENABLED=false se respeta" "false" "$RCON_ENABLED"
assert_eq "RCON_PORT usa el default 25575 si falta" "25575" "$RCON_PORT"

# ------------------------------------------------------------- mc_server_name
section "mc_server_name"
unset SERVER_NAME
assert_eq "usa el nombre de carpeta sin SERVER_NAME" "$(basename "$T1")" "$(mc_server_name "$T1")"
SERVER_NAME="MiServidor"
assert_eq "usa SERVER_NAME si está definido" "MiServidor" "$(mc_server_name "$T1")"
unset SERVER_NAME

# ------------------------------------------------------------- mc_runtime_dir
section "mc_runtime_dir"
RT1=$(mc_runtime_dir "$T1")
RT1_AGAIN=$(mc_runtime_dir "$T1")
assert_eq "mismo path en llamadas repetidas" "$RT1" "$RT1_AGAIN"
assert_eq "el directorio realmente existe" "si" "$([ -d "$RT1" ] && echo si || echo no)"
RT2=$(mc_runtime_dir "$T2")
assert_fails "runtime dirs de carpetas distintas no deben coincidir" [ "$RT1" = "$RT2" ]

if command -v python3 >/dev/null 2>&1 && [ -f "$DIR/mcconfig.py" ]; then
    PY_RT=$(cd "$DIR" && python3 -c "import mcconfig; print(mcconfig.runtime_dir('$T1'))" 2>/dev/null)
    assert_eq "coincide con mcconfig.runtime_dir (Python, misma fórmula)" "$RT1" "$PY_RT"
fi

# -------------------------------------------------------------- mc_detect_jar
section "mc_detect_jar"
assert_fails "falla si no hay ningún jar" mc_detect_jar "$T3"
touch "$T3/server.jar"
assert_eq "detecta server.jar" "server.jar" "$(mc_detect_jar "$T3")"
touch "$T3/fabric-server-launch.jar"
assert_eq "prioriza fabric-server-launch.jar sobre server.jar" "fabric-server-launch.jar" "$(mc_detect_jar "$T3")"

touch "$T4/algo.jar" "$T4/otroalgo.jar"
assert_fails "ambiguo con dos jars sin nombre reconocido" mc_detect_jar "$T4"

touch "$T5/miservidor-custom.jar"
assert_eq "detecta el único jar aunque el nombre no sea estándar" "miservidor-custom.jar" "$(mc_detect_jar "$T5")"

# -------------------------------------------------------- mc_resolve_launch_cmd
section "mc_resolve_launch_cmd"
JVM_ARGS="-Xmx1G"; SERVER_JAR=""; SERVER_START_CMD=""
assert_eq "autodetecta jar y construye el comando java" \
    "java -Xmx1G -jar miservidor-custom.jar nogui" "$(mc_resolve_launch_cmd "$T5")"

SERVER_START_CMD="./run.sh nogui"
assert_eq "SERVER_START_CMD tiene prioridad (escape hatch Forge/NeoForge)" \
    "./run.sh nogui" "$(mc_resolve_launch_cmd "$T5")"
SERVER_START_CMD=""

SERVER_JAR="no-existe-en-disco.jar"
assert_fails "falla si SERVER_JAR no existe en disco" mc_resolve_launch_cmd "$T5"
SERVER_JAR=""

# -------------------------------------------------------------- mc_find_java_pid
section "mc_find_java_pid (proceso 'java' real, vía JEP 330 single-file launch)"
if command -v java >/dev/null 2>&1; then
    cat > "$T2/Sleep.java" <<'EOF'
public class Sleep { public static void main(String[] a) throws Exception { Thread.sleep(30000); } }
EOF
    ( cd "$T2" && exec java Sleep.java ) &
    JPID=$!
    sleep 1.5
    FOUND=$(mc_find_java_pid "$T2")
    assert_eq "encuentra el proceso java real por su cwd" "$JPID" "$FOUND"
    assert_fails "no encuentra nada en un directorio sin proceso java" mc_find_java_pid "$T3"
    kill "$JPID" 2>/dev/null
    wait "$JPID" 2>/dev/null
else
    echo "  (saltado: no hay 'java' en PATH)"
fi

echo
echo "================================"
echo "lib.sh: $PASS OK, $FAIL fallos"
[ "$FAIL" -eq 0 ]
