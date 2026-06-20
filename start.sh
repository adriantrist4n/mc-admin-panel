#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

source "$DIR/lib.sh"
source "$DIR/config.sh"

if [ "$1" = "--direct" ]; then
    launch_cmd=$(mc_resolve_launch_cmd "$DIR")
    if [ -z "$launch_cmd" ]; then
        echo "No se pudo determinar cómo arrancar el servidor. Define SERVER_JAR o SERVER_START_CMD en config.sh." >&2
        exit 1
    fi
    exec $launch_cmd
fi

exec ./admin.sh
