#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

source "$DIR/lib.sh"
source "$DIR/config.sh"

mc_rcon_creds "$DIR"   # define RCON_ENABLED / RCON_PORT / RCON_PASSWORD

RUNTIME_DIR=$(mc_runtime_dir "$DIR")
DISPLAY_NAME=$(mc_server_name "$DIR")

export MC_ADMIN_RUNTIME_DIR="$RUNTIME_DIR"
export MC_ADMIN_SERVER_NAME="$DISPLAY_NAME"
export RCON_PORT RCON_PASSWORD IDLE_TIMEOUT IDLE_CHECK_INTERVAL

export NEWT_COLORS="
root=,black
window=white,black
border=brightcyan,black
shadow=,gray
title=brightmagenta,black
button=black,brightcyan
actbutton=black,yellow
compactbutton=brightcyan,black
checkbox=brightcyan,black
actcheckbox=black,brightcyan
entry=white,black
label=brightcyan,black
listbox=white,black
actlistbox=black,brightcyan
sellistbox=yellow,black
actsellistbox=black,brightcyan
textbox=white,black
acttextbox=black,brightcyan
helpline=brightcyan,black
roottext=brightcyan,black
"

SERVER_PID_FILE="$RUNTIME_DIR/server.pid"
CONSOLE_LOG="$RUNTIME_DIR/console.log"
IDLE_PID_FILE="$RUNTIME_DIR/idle-monitor.pid"

VENV_DIR="$DIR/.venv-admin"
VENV_PY="$VENV_DIR/bin/python"
DASHBOARD="$DIR/dashboard.py"
DASHBOARD_OK=0

# Intérprete para el núcleo Python (mcadmin.py): el del venv si existe (lleva
# psutil), si no el del sistema.
mc_py() {
    if [ -x "$VENV_PY" ]; then echo "$VENV_PY"; else echo "python3"; fi
}

# server_running: comprueba el PID file namespaced; si no existe, intenta
# adoptar un proceso java ya en marcha en este mismo directorio (por cwd) en
# vez de asumir que está parado — evita lanzar una segunda instancia tras
# cambios en el esquema de rutas o si admin.sh se reinicia.
server_running() {
    local pid
    if [ -f "$SERVER_PID_FILE" ]; then
        pid=$(cat "$SERVER_PID_FILE" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$SERVER_PID_FILE"
    fi
    pid=$(mc_find_java_pid "$DIR") || return 1
    echo "$pid" > "$SERVER_PID_FILE"
    return 0
}

rcon_cmd() {
    local cmd="$*"
    python3 "$DIR/rcon.py" "127.0.0.1" "$RCON_PORT" "$RCON_PASSWORD" "$cmd" 2>/dev/null
}

# Prepara el venv del dashboard (rich). Reutiliza el psutil del sistema.
# Solo crea/instala la primera vez; si falla, se usa el panel básico.
ensure_venv() {
    if [ -x "$VENV_PY" ] && "$VENV_PY" -c "import rich" 2>/dev/null; then
        DASHBOARD_OK=1
        return 0
    fi
    whiptail --title "Panel" --infobox "Instalando dependencias del panel (rich)...\nSolo ocurre la primera vez." 7 60
    if python3 -m venv --system-site-packages "$VENV_DIR" >/dev/null 2>&1 \
        && "$VENV_PY" -m pip install --quiet rich >/dev/null 2>&1 \
        && "$VENV_PY" -c "import rich" 2>/dev/null; then
        DASHBOARD_OK=1
    else
        DASHBOARD_OK=0
        whiptail --title "Aviso" --msgbox "No se pudo preparar el dashboard avanzado (¿sin conexión?).\nSe usará el panel básico." 9 62
    fi
}

# Lanza el dashboard en vivo. Devuelve su código de salida (0 = salir).
launch_dashboard() {
    clear
    "$VENV_PY" "$DASHBOARD"
    return $?
}

# Lógica de arranque/parada/reinicio sin UI: delega en el núcleo
# multiplataforma (mcadmin.py) para no duplicar la lógica con Windows.
# Códigos: 0 ok · 1 error · 2 no-op (ya en marcha / no estaba en marcha).
_do_start() {
    "$(mc_py)" "$DIR/mcadmin.py" --do-start
}

# Wrapper whiptail (fallback sin rich)
start_server() {
    if server_running; then
        whiptail --title "Error" --msgbox "El servidor ya está en ejecución. PID: $(cat "$SERVER_PID_FILE")" 8 50
        return 1
    fi
    whiptail --title "Iniciando" --infobox "Arrancando servidor Minecraft..." 5 50
    if _do_start >/dev/null 2>&1; then
        whiptail --title "Servidor" --msgbox "Servidor iniciado correctamente.\nPID: $(cat "$SERVER_PID_FILE" 2>/dev/null)" 8 50
    else
        whiptail --title "Error" --msgbox "El servidor no respondió en 60s. Revisa:\n$CONSOLE_LOG" 10 60
    fi
}

_do_stop() {
    "$(mc_py)" "$DIR/mcadmin.py" --do-stop
}

_do_restart() {
    "$(mc_py)" "$DIR/mcadmin.py" --do-restart
}

# Wrapper whiptail (fallback sin rich)
stop_server() {
    if ! server_running; then
        whiptail --title "Error" --msgbox "El servidor no está en ejecución." 8 40
        return 1
    fi
    whiptail --title "Deteniendo" --infobox "Enviando comando 'stop' al servidor..." 5 50
    _do_stop >/dev/null 2>&1
    whiptail --title "Servidor" --msgbox "Servidor detenido." 8 40
}

show_status() {
    if ! server_running; then
        whiptail --title "Estado" --msgbox "El servidor NO está en ejecución." 8 40
        return
    fi

    local pid uptime players ram players_raw
    pid=$(cat "$SERVER_PID_FILE")

    local now
    now=$(date +%s)
    local start_time
    start_time=$(stat -c %Y /proc/"$pid" 2>/dev/null || echo "$now")
    local elapsed=$((now - start_time))
    local uptime_str
    uptime_str=$(printf "%dd %dh %dm" $((elapsed/86400)) $(((elapsed%86400)/3600)) $(((elapsed%3600)/60)))

    if [ "$RCON_ENABLED" = "true" ]; then
        players_raw=$(rcon_cmd "list")
        players=$(echo "$players_raw" | grep -oP 'There are \K\d+' 2>/dev/null || echo "?")
    else
        players="? (RCON desactivado)"
    fi

    ram=$(ps -o rss= -p "$pid" 2>/dev/null | awk '{printf "%.1f GB", $1/1048576}' || echo "?")

    STATUS="PID: $pid
Uptime: $uptime_str
Jugadores: $players
RAM: $ram"

    whiptail --title "Estado del Servidor" --msgbox "$STATUS" 12 50
}

send_command() {
    if ! server_running; then
        whiptail --title "Error" --msgbox "El servidor no está en ejecución." 8 40
        return
    fi
    if [ "$RCON_ENABLED" != "true" ]; then
        whiptail --title "Error" --msgbox "RCON está desactivado en server.properties.\nActiva enable-rcon=true y fija rcon.password para usar esto." 9 60
        return
    fi
    local cmd
    cmd=$(whiptail --title "Consola" --inputbox "Escribe el comando (sin /):" 8 50 3>&1 1>&2 2>&3)
    if [ -z "$cmd" ]; then
        return
    fi
    local result
    result=$(rcon_cmd "$cmd")
    if [ -n "$result" ]; then
        whiptail --title "Resultado" --msgbox "$result" 15 60
    else
        whiptail --title "Resultado" --msgbox "(comando ejecutado, sin respuesta)" 8 40
    fi
}

view_logs() {
    if [ ! -f "$DIR/logs/latest.log" ]; then
        whiptail --title "Error" --msgbox "No se encuentra logs/latest.log" 8 40
        return
    fi
    local logs
    logs=$(tail -n 30 "$DIR/logs/latest.log")
    whiptail --title "Últimos 30 logs" --scrolltext --msgbox "$logs" 20 70
}

attach_console() {
    if ! server_running; then
        whiptail --title "Error" --msgbox "El servidor no está en ejecución." 8 40
        return
    fi
    whiptail --title "Consola en vivo" --msgbox "Mostrando consola en vivo. Presiona Ctrl+C para volver al menú." 8 60
    clear
    tail -f "$CONSOLE_LOG"
}

restart_server() {
    whiptail --title "Reiniciando" --infobox "Reiniciando servidor..." 5 40
    if server_running && [ "$RCON_ENABLED" = "true" ]; then
        rcon_cmd "say §cReiniciando servidor..."
        sleep 1
    fi
    stop_server
    start_server
}

backup_now() {
    if ! server_running; then
        whiptail --title "Backup" --yesno "El servidor está detenido.\n¿Hacer backup del mundo igualmente?" 8 52 || return
    fi
    whiptail --title "Backup" --infobox "Creando backup del mundo...\nEsto puede tardar un poco." 6 52
    if "$DIR/backup.sh" >/dev/null 2>&1; then
        local last size
        last=$(ls -1t "$DIR/backups"/world-*.tar.gz 2>/dev/null | head -1)
        size=$(du -h "$last" 2>/dev/null | cut -f1)
        whiptail --title "Backup" --msgbox "Backup completado:\n$(basename "$last") ($size)" 9 60
    else
        whiptail --title "Backup" --msgbox "El backup falló. Revisa backups/backup.log" 8 60
    fi
}

# Menú de control (whiptail). Devuelve:
#   0 = volver al dashboard · 1 = salir del panel
control_menu() {
    while true; do
        local running_text
        if server_running; then
            running_text="🟢 EN EJECUCIÓN  ·  PID $(cat "$SERVER_PID_FILE")"
        else
            running_text="🔴 DETENIDO"
        fi

        local first_label
        if [ "$DASHBOARD_OK" = "1" ]; then
            first_label="📊 Volver al dashboard en vivo"
        else
            first_label="📈 Estado del servidor (texto)"
        fi

        local choice st
        choice=$(whiptail --title "🎮 $DISPLAY_NAME · Control" \
            --cancel-button "Dashboard" \
            --menu "\n  $running_text\n" 21 66 11 \
            "dashboard" "$first_label" \
            "cmd"       "💬 Enviar comando RCON" \
            "console"   "⌨️  Consola en vivo (tail)" \
            "logs"      "📜 Ver últimos logs" \
            "status"    "📈 Estado (texto)" \
            "backup"    "💾 Backup del mundo ahora" \
            "start"     "🟢 Iniciar servidor" \
            "stop"      "🔴 Detener servidor" \
            "restart"   "🔄 Reiniciar servidor" \
            "quit"      "🚪 Salir del panel" \
            3>&1 1>&2 2>&3)
        st=$?
        if [ $st -ne 0 ]; then
            return 0   # ESC / botón Dashboard -> volver al dashboard
        fi

        case "$choice" in
            dashboard)
                if [ "$DASHBOARD_OK" = "1" ]; then
                    return 0
                else
                    show_status
                fi ;;
            cmd) send_command ;;
            console) attach_console ;;
            logs) view_logs ;;
            status) show_status ;;
            backup) backup_now ;;
            start) start_server ;;
            stop) stop_server ;;
            restart) restart_server ;;
            quit) return 1 ;;
            *) return 0 ;;
        esac
    done
}

# Bucle principal. Con rich, el menú vive dentro de dashboard.py (un solo proceso).
panel_loop() {
    if [ "$DASHBOARD_OK" != "1" ]; then
        control_menu   # modo básico (sin rich): solo el menú whiptail
        return
    fi
    launch_dashboard
    local rc=$?
    if [ "$rc" != "0" ]; then
        whiptail --title "Panel" --msgbox "El panel terminó con error (código $rc).\nAbriendo el menú básico." 9 62
        control_menu
    fi
}

# Dispatch headless: dashboard.py invoca la lógica de lifecycle sin UI y sale.
case "$1" in
    --do-start)   _do_start;       exit $? ;;
    --do-stop)    _do_stop;        exit $? ;;
    --do-restart) _do_restart;     exit $? ;;
    --running)    server_running;  exit $? ;;
esac

# ---- Arranque del panel ----
ensure_venv

if [ "$RCON_ENABLED" != "true" ]; then
    whiptail --title "Aviso: RCON desactivado" --msgbox "RCON está desactivado en server.properties.\nMuchas funciones del panel (consola, TPS, jugadores...) lo necesitan.\n\nPara activarlo, edita server.properties:\n  enable-rcon=true\n  rcon.password=algo-secreto\ny reinicia el servidor." 14 64
fi

# Dashboard directo: si el servidor está parado, ofrecer arrancarlo y entrar al panel.
if ! server_running; then
    if whiptail --title "$DISPLAY_NAME" --yesno "El servidor no está en ejecución.\n¿Quieres iniciarlo ahora?" 8 52; then
        start_server
    fi
fi

panel_loop
clear
