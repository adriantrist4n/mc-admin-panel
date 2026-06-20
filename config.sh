#!/bin/bash
# Configuración del panel. Es el único archivo que normalmente hay que tocar
# al instalar el toolkit en un servidor nuevo. Lo leen admin.sh, start.sh,
# backup.sh e install-service.sh.

# Nombre que se muestra en el dashboard y en el servicio systemd.
# Si lo dejas vacío, se usa el nombre de esta carpeta.
SERVER_NAME="Algueys"

# Memoria para la JVM. Ajusta JVM_RAM a tu máquina (deja ~2GB libres para el
# sistema). JVM_ARGS se puede personalizar libremente (flags de GC, etc).
JVM_RAM="8G"
JVM_ARGS="-Xms2G -Xmx${JVM_RAM} -XX:+UseZGC -XX:ZUncommitDelay=30"

# Jar del servidor. Déjalo en blanco para autodetectarlo (busca nombres
# habituales como fabric-server-launch.jar/server.jar/paper.jar/purpur.jar/
# spigot.jar, o el único .jar que haya en esta carpeta). Si la detección
# falla o tienes varios jars, fíjalo aquí a mano.
SERVER_JAR=""

# Escape hatch: si lo defines, se usa este comando completo para arrancar el
# servidor en vez de "java $JVM_ARGS -jar $SERVER_JAR nogui". Pensado para
# Forge/NeoForge moderno (que arrancan vía run.sh) o cualquier caso especial.
# Ejemplo: SERVER_START_CMD="./run.sh nogui"
SERVER_START_CMD=""

# Modo ahorro: cuando el servidor lleva IDLE_TIMEOUT segundos vacío, baja
# dificultad/mobs/clima para consumir menos hasta que entre alguien.
IDLE_ENABLED=true
IDLE_TIMEOUT=300
IDLE_CHECK_INTERVAL=30

# Backups del mundo: cuántos se conservan antes de borrar los más antiguos.
MAX_BACKUPS=14
