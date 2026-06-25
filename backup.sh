#!/bin/bash
# Backup del mundo. Delega en el núcleo multiplataforma (mcadmin.py) para no
# duplicar la lógica con Windows: pausa el autoguardado vía RCON, comprime el
# mundo a backups/world-FECHA.tar.gz, reactiva el guardado y conserva los
# MAX_BACKUPS más recientes (config.sh). Lo usa el menú del panel y el timer
# de systemd.
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

PY="$DIR/.venv-admin/bin/python"
[ -x "$PY" ] || PY=python3

exec "$PY" "$DIR/mcadmin.py" --backup
