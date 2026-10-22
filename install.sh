#!/usr/bin/env bash
# thinkISP: instalación de usuario, sin cambiar paquetes ni la configuración de red.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$(uname -s)" != Linux ]]; then
    echo 'thinkISP requiere Linux con NetworkManager.' >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Falta Python 3.9 o posterior.' >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
    echo 'thinkISP requiere Python 3.9 o posterior.' >&2
    exit 1
fi
THINKISP_PYTHON="$(python3 -c 'import sys; print(sys.executable)')"
printf 'thinkISP · Instalación de usuario\nPython: %s\n' "$THINKISP_PYTHON"

missing_tools=0
for tool in nmcli iw ip; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'Falta herramienta requerida: %s\n' "$tool" >&2
        missing_tools=1
    fi
done
if [[ "$missing_tools" != 0 ]]; then
    echo 'Instala las herramientas con el gestor de paquetes de tu distribución.' >&2
    exit 1
fi
# Ejecutar el parser offline realmente: --help no prueba que se pueda crear el perfil.
if ! nmcli --offline connection add type wifi con-name thinkISP ssid thinkISP \
    802-11-wireless.mode ap ipv4.method shared \
    802-11-wireless-security.key-mgmt wpa-psk >/dev/null; then
    echo 'Se necesita nmcli con perfiles --offline (NetworkManager 1.40 o posterior).' >&2
    exit 1
fi
for tool in tc curl qrencode dig ethtool notify-send nft pkexec; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'Opcional no disponible: %s (sólo afecta a las funciones que lo utilizan).\n' "$tool"
    fi
done
if ! "$THINKISP_PYTHON" -c 'import curses' 2>/dev/null; then
    echo 'El panel necesita el módulo curses de Python; los comandos CLI siguen disponibles.'
fi

INSTALL_DIR="${THINKISP_BIN_DIR:-${HOME}/.local/bin}"
DATA_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}"
CONFIG_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}"
ZSH_DIR="${THINKISP_ZSH_DIR:-${HOME}/.zsh/completion}"
for directory in "$INSTALL_DIR" "$DATA_DIR" "$CONFIG_DIR" "$ZSH_DIR"; do
    if [[ "$directory" != /* ]]; then
        echo 'Las rutas de instalación deben ser absolutas.' >&2
        exit 1
    fi
done
mkdir -p "$INSTALL_DIR"
# %q evita que espacios, comillas, $, & o | en la ruta se conviertan en código.
{
    printf '#!/usr/bin/env bash\n'
    printf 'THINKISP_DIR=%q\nTHINKISP_PYTHON=%q\n' "$PROJECT_DIR" "$THINKISP_PYTHON"
    printf 'exec "$THINKISP_PYTHON" "$THINKISP_DIR/main.py" "$@"\n'
} > "$INSTALL_DIR/thinkisp"
chmod 755 "$INSTALL_DIR/thinkisp"
ln -sf "$INSTALL_DIR/thinkisp" "$INSTALL_DIR/isp"

mkdir -p "$DATA_DIR/bash-completion/completions" "$CONFIG_DIR/fish/completions" "$ZSH_DIR" "$DATA_DIR/applications"
cp "$PROJECT_DIR/extras/completions/thinkisp.bash" "$DATA_DIR/bash-completion/completions/thinkisp"
ln -sf "$DATA_DIR/bash-completion/completions/thinkisp" "$DATA_DIR/bash-completion/completions/isp"
cp "$PROJECT_DIR/extras/completions/thinkisp.fish" "$CONFIG_DIR/fish/completions/thinkisp.fish"
ln -sf "$CONFIG_DIR/fish/completions/thinkisp.fish" "$CONFIG_DIR/fish/completions/isp.fish"
cp "$PROJECT_DIR/extras/completions/_thinkisp" "$ZSH_DIR/_thinkisp"

"$THINKISP_PYTHON" - "$PROJECT_DIR/extras/thinkisp.desktop" "$DATA_DIR/applications/thinkisp.desktop" "$INSTALL_DIR/thinkisp" <<'PY'
from pathlib import Path
import sys
source, target, executable = sys.argv[1:]
# Desktop Entry procesa primero escapes generales y luego la sintaxis de Exec.
quoted = '"' + ''.join('\\' + c if c in '\\"`$' else c for c in executable) + '"'
quoted = quoted.replace('\\', '\\\\').replace('%', '%%').replace('\n', '\\n').replace('\r', '\\r')
lines = []
for line in Path(source).read_text().splitlines():
    if line.startswith('Exec='):
        arguments = line[5:].split()[1:]
        line = 'Exec=' + quoted + (' ' + ' '.join(arguments) if arguments else '')
    lines.append(line)
Path(target).write_text('\n'.join(lines) + '\n')
PY
printf '\nInstalado: %s/thinkisp\nLanzador: %s/applications/thinkisp.desktop\n' "$INSTALL_DIR" "$DATA_DIR"
if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    printf 'Añade %s al PATH de tu shell para usar thinkisp e isp.\n' "$INSTALL_DIR"
fi
printf 'Zsh: añade %s al fpath antes de ejecutar compinit.\n' "$ZSH_DIR"
echo 'La primera ejecución abre la configuración. Diagnóstico sin cambiar la red: thinkisp doctor'
echo 'Conserva la carpeta del proyecto: los lanzadores ejecutan sus archivos directamente.'
