# mc-admin-panel

Panel de administración por terminal para un servidor de Minecraft en Linux,
con un dashboard en vivo (CPU, RAM, heap de la JVM, TPS/MSPT, jugadores,
actividad reciente...) y un menú de control integrado (comandos RCON, consola,
logs, backups, arrancar/detener/reiniciar).

Pensado para **vanilla, Paper, Purpur, Spigot o Fabric**: se instala dentro de
la carpeta raíz de tu servidor (donde está el `.jar` y `server.properties`) y
se adapta solo.

## Requisitos

- Linux (usa `/proc` para detectar procesos; no funciona en macOS/Windows).
- `whiptail` (casi siempre viene preinstalado; si no, `apt install whiptail` /
  equivalente).
- Python 3 y el módulo `psutil` (`pip install psutil` o el paquete de tu
  distro, p. ej. `python3-psutil`).
- Un JDK con `jcmd`/`jstat` (los trae cualquier JDK normal; no vale un JRE
  recortado).
- RCON habilitado en tu servidor (ver más abajo). El panel funciona sin RCON,
  pero pierdes TPS, jugadores, consola remota y el modo ahorro.

## Instalación

1. Copia (o clona) los archivos de este repositorio dentro de la carpeta raíz
   de tu servidor de Minecraft, junto al `.jar` y `server.properties`.
2. En `server.properties`, asegúrate de tener:
   ```
   enable-rcon=true
   rcon.password=algo-secreto
   rcon.port=25575
   ```
3. Edita `config.sh` (es el único archivo que normalmente hay que tocar):
   - `SERVER_NAME`: nombre que se muestra en el dashboard.
   - `JVM_RAM`: ajusta la memoria a tu máquina.
   - `SERVER_JAR`: déjalo vacío para autodetectarlo, o fíjalo si tienes varios
     `.jar` en la carpeta.
   - `SERVER_START_CMD`: solo si necesitas un comando de arranque distinto
     (ver "Limitaciones" más abajo).
4. Arranca el panel:
   ```
   ./admin.sh
   ```
   La primera vez crea un entorno virtual local (`.venv-admin/`) e instala
   `rich` (reutiliza el `psutil` del sistema). Si no hay conexión para
   instalarlo, el panel sigue funcionando en un modo básico con `whiptail`.

## Uso

`./admin.sh` abre directamente el **dashboard en vivo**. Atajos:

- `q` / `Enter` — abrir el menú de control
- `Q` / `Ctrl-C` — salir del panel

Dentro del menú: `↑↓` para moverte, `Enter` o el número para elegir, `q`/`Esc`
para volver al dashboard. Las opciones disponibles incluyen enviar un comando
RCON, ver la consola en vivo, ver logs, hacer un backup ahora, y
arrancar/detener/reiniciar el servidor.

## Servicio systemd (opcional)

Para que el servidor arranque solo al encender la máquina y se reinicie si se
cae:

```
sudo ./install-service.sh
```

El nombre del servicio se deriva del nombre de la carpeta (p. ej.
`minecraft-miservidor`), para poder instalar varias instancias en la misma
máquina sin que choquen entre sí. Esto también instala un timer de backup
automático cada 6 horas y rotación de logs. Al terminar, el script imprime los
comandos `systemctl`/`journalctl` exactos a usar.

## Backups

`./backup.sh` comprime el mundo (el nombre de carpeta se lee de `level-name`
en `server.properties`, no se asume "world") a `backups/world-FECHA.tar.gz`,
pausando el autoguardado mientras tanto si RCON está disponible. Se conservan
los `MAX_BACKUPS` más recientes (configurable en `config.sh`); también se
puede lanzar desde el menú del panel.

## Modo ahorro

Si el servidor lleva varios minutos vacío (`IDLE_TIMEOUT` en `config.sh`, 5
minutos por defecto), `idle-monitor.py` baja la dificultad a pacífico y
desactiva mobs/clima/random ticks para consumir menos CPU, y lo restaura en
cuanto entra alguien. Se puede desactivar con `IDLE_ENABLED=false` en
`config.sh`. Necesita RCON.

## Limitaciones conocidas

- Pensado para servidores de un solo `.jar` lanzado con
  `java -jar archivo.jar nogui` (vanilla/Paper/Purpur/Spigot/Fabric). El
  Forge/NeoForge moderno arranca con un script `run.sh` generado por el
  instalador; en ese caso define `SERVER_START_CMD="./run.sh nogui"` en
  `config.sh` en vez de depender de la autodetección de jar.
- Solo Linux (usa `/proc/<pid>/cwd` para reconocer el proceso del servidor).
- La detección de la versión del servidor en el dashboard es "best effort"
  (mira la carpeta `versions/`, típica de Fabric); si no la encuentra,
  simplemente no la muestra.

## Licencia

MIT — ver [LICENSE](LICENSE).
