#!/bin/bash
# Ejecuta toda la suite de tests del toolkit (lib.sh + los módulos Python).
# No toca el servidor de Minecraft real: todo corre contra directorios
# temporales y, cuando hace falta un proceso "java" o un servidor RCON, usa
# uno falso/sintético levantado por el propio test.
#
# Uso: ./tests/run_tests.sh
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR" || exit 1

PY="$DIR/.venv-admin/bin/python"
[ -x "$PY" ] || PY="python3"

FAIL=0

echo "########## lib.sh ##########"
bash "$DIR/tests/test_lib.sh" || FAIL=1

for t in test_mcconfig.py test_dashboard.py test_idle_monitor.py test_rcon.py; do
    echo
    echo "########## $t ##########"
    "$PY" "$DIR/tests/$t" -v || FAIL=1
done

echo
if [ "$FAIL" -eq 0 ]; then
    echo "✅ Todos los tests pasaron."
else
    echo "❌ Algún test falló."
fi
exit $FAIL
