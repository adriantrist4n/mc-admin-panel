#!/bin/bash
# Instala el servidor como servicio systemd (arranque automático + backups
# programados). El nombre del servicio se deriva del nombre de esta carpeta,
# para poder instalar varias instancias en la misma máquina sin colisionar.
# Uso: sudo ./install-service.sh [nombre-de-servicio]
DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$EUID" -ne 0 ]; then
    echo "Ejecutar como root: sudo $0"
    exit 1
fi

source "$DIR/lib.sh"
source "$DIR/config.sh"

# Usuario/grupo reales (no "root"): el que invocó sudo, o el actual si no hay sudo.
TARGET_USER="${SUDO_USER:-$(whoami)}"
TARGET_GROUP="$(id -gn "$TARGET_USER" 2>/dev/null || echo "$TARGET_USER")"

DISPLAY_NAME=$(mc_server_name "$DIR")

RAW_NAME="$(basename "$DIR")"
SLUG=$(echo "$RAW_NAME" | tr '[:upper:]' '[:lower:]' | tr -c '[:alnum:]' '-' | sed 's/-\+/-/g; s/^-//; s/-$//')
SERVICE_NAME="${1:-minecraft-${SLUG:-server}}"

echo "Usuario/grupo: $TARGET_USER:$TARGET_GROUP"
echo "Servicio: $SERVICE_NAME"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=Servidor Minecraft ($DISPLAY_NAME)
After=network.target

[Service]
Type=simple
User=$TARGET_USER
Group=$TARGET_GROUP
WorkingDirectory=$DIR
ExecStart=$DIR/start.sh --direct
Restart=on-failure
RestartSec=10
Nice=-1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > "/etc/systemd/system/${SERVICE_NAME}-backup.service" << EOF
[Unit]
Description=Backup del mundo ($DISPLAY_NAME)

[Service]
Type=oneshot
User=$TARGET_USER
Group=$TARGET_GROUP
WorkingDirectory=$DIR
ExecStart=$DIR/backup.sh
EOF

cat > "/etc/systemd/system/${SERVICE_NAME}-backup.timer" << EOF
[Unit]
Description=Backup automático del mundo ($DISPLAY_NAME) cada 6 horas

[Timer]
OnCalendar=*-*-* 00,06,12,18:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > "/etc/logrotate.d/${SERVICE_NAME}" << EOF
$DIR/logs/latest.log {
    su $TARGET_USER $TARGET_GROUP
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
}
$DIR/backups/backup.log {
    su $TARGET_USER $TARGET_GROUP
    weekly
    rotate 4
    compress
    missingok
    notifempty
}
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl enable "${SERVICE_NAME}-backup.timer"
systemctl start "${SERVICE_NAME}-backup.timer"

echo
echo "Instalado. Comandos:"
echo "  systemctl start $SERVICE_NAME              # Iniciar servidor"
echo "  systemctl stop $SERVICE_NAME               # Detener servidor"
echo "  systemctl status $SERVICE_NAME             # Ver estado"
echo "  journalctl -u $SERVICE_NAME -f              # Logs en vivo"
echo "  systemctl start ${SERVICE_NAME}-backup       # Backup manual"
