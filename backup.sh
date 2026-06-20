#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

source "$DIR/lib.sh"
source "$DIR/config.sh"
mc_rcon_creds "$DIR"   # define RCON_ENABLED / RCON_PORT / RCON_PASSWORD

BACKUP_DIR="$DIR/backups"
WORLD_DIR=$(mc_world_dir "$DIR")
WORLD_NAME=$(basename "$WORLD_DIR")
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/world-$TIMESTAMP.tar.gz"

mkdir -p "$BACKUP_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$BACKUP_DIR/backup.log"
}

log "Iniciando backup..."

if [ "$RCON_ENABLED" = "true" ]; then
    # Pausar guardado automático y forzar save
    python3 "$DIR/rcon.py" "127.0.0.1" "$RCON_PORT" "$RCON_PASSWORD" "save-off" 2>/dev/null
    python3 "$DIR/rcon.py" "127.0.0.1" "$RCON_PORT" "$RCON_PASSWORD" "save-all" 2>/dev/null
    sleep 2
    python3 "$DIR/rcon.py" "127.0.0.1" "$RCON_PORT" "$RCON_PASSWORD" "save-off" 2>/dev/null
fi

# Comprimir mundo
tar czf "$BACKUP_FILE" -C "$DIR" "$WORLD_NAME/"
RC=$?

if [ "$RCON_ENABLED" = "true" ]; then
    # Reactivar guardado
    python3 "$DIR/rcon.py" "127.0.0.1" "$RCON_PORT" "$RCON_PASSWORD" "save-on" 2>/dev/null
fi

if [ $RC -eq 0 ] && [ -f "$BACKUP_FILE" ]; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    log "Backup completado: $BACKUP_FILE ($SIZE)"
else
    log "ERROR: Falló el backup"
    exit 1
fi

# Limpiar backups viejos
COUNT=$(ls -1 "$BACKUP_DIR"/world-*.tar.gz 2>/dev/null | wc -l)
if [ "$COUNT" -gt "$MAX_BACKUPS" ]; then
    ls -1t "$BACKUP_DIR"/world-*.tar.gz | tail -n +$((MAX_BACKUPS + 1)) | while read -r old; do
        rm -f "$old"
        log "Backup antiguo eliminado: $old"
    done
fi

log "---"
