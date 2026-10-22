#!/usr/bin/env bash
# ==========================================================
# ThinkISP - Instalador Rápido de 1 Paso
# ==========================================================

set -e

BOLD="\033[1m"
GREEN="\033[1;32m"
CYAN="\033[1;36m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
RESET="\033[0m"

echo -e "\n${BOLD}${CYAN}═══════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}${CYAN}   🚀 INSTALADOR THINKISP - PORTABLE ROUTER SUITE          ${RESET}"
echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════════${RESET}\n"

# 1. Comprobar Python 3
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}✖ Error: Python 3 no está instalado.${RESET}"
    exit 1
fi
echo -e "  ${GREEN}✔ Python 3 detectado:${RESET} $(python3 --version)"

# 2. Comprobar herramientas clave
for tool in nmcli iw ip curl; do
    if command -v "$tool" &>/dev/null; then
        echo -e "  ${GREEN}✔ $tool detectado.${RESET}"
    else
        echo -e "  ${RED}✖ Advertencia: $tool no está instalado.${RESET}"
    fi
done

# 3. Herramientas opcionales recomendadas
for opt in qrencode dig ethtool notify-send; do
    if command -v "$opt" &>/dev/null; then
        echo -e "  ${GREEN}✔ $opt (opcional) disponible.${RESET}"
    else
        echo -e "  ${YELLOW}ℹ $opt no encontrado (recomendado para QR / Speedtest / DNS).${RESET}"
    fi
done

# 4. Directorio destino
INSTALL_DIR="${HOME}/.local/bin"
mkdir -p "$INSTALL_DIR"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Crear wrapper ejecutable
cat << 'EOF' > "$INSTALL_DIR/thinkisp"
#!/usr/bin/env bash
THINKISP_DIR="%%PROJECT_DIR%%"
export PYTHONPATH="$THINKISP_DIR:$PYTHONPATH"
exec python3 "$THINKISP_DIR/main.py" "$@"
EOF

sed -i "s|%%PROJECT_DIR%%|$PROJECT_DIR|g" "$INSTALL_DIR/thinkisp"
chmod +x "$INSTALL_DIR/thinkisp"

# Crear enlace simbólico 'isp'
ln -sf "$INSTALL_DIR/thinkisp" "$INSTALL_DIR/isp"

echo -e "\n  ${GREEN}✔ Binarios instalados en:${RESET} ${BOLD}$INSTALL_DIR/thinkisp${RESET} y ${BOLD}$INSTALL_DIR/isp${RESET}"

# 5. Comprobar si ~/.local/bin está en el PATH
if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    echo -e "  ${YELLOW}⚠ Aviso: $INSTALL_DIR no parece estar en tu PATH.${RESET}"
    echo -e "    Agrega la siguiente línea a tu ~/.bashrc o ~/.config/fish/config.fish:"
    echo -e "    ${CYAN}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}"
fi

# 6. Configuración de Autocompletado de Shell (Zsh, Bash, Fish)
echo -e "\n  ${BOLD}Configuración de Autocompletado de Shell:${RESET}"

# Zsh completion
ZSH_SRC="$PROJECT_DIR/extras/completions/_thinkisp"
if [ -f "$ZSH_SRC" ]; then
    if [ -w "/usr/local/share/zsh/site-functions" ]; then
        cp "$ZSH_SRC" "/usr/local/share/zsh/site-functions/_thinkisp"
        chmod 644 "/usr/local/share/zsh/site-functions/_thinkisp"
        echo -e "  ${GREEN}✔ Autocompletado Zsh instalado en:${RESET} /usr/local/share/zsh/site-functions/_thinkisp"
    elif [ -w "/usr/share/zsh/site-functions" ]; then
        cp "$ZSH_SRC" "/usr/share/zsh/site-functions/_thinkisp"
        chmod 644 "/usr/share/zsh/site-functions/_thinkisp"
        echo -e "  ${GREEN}✔ Autocompletado Zsh instalado en:${RESET} /usr/share/zsh/site-functions/_thinkisp"
    else
        ZSH_USER_DIR="${HOME}/.zsh/completion"
        mkdir -p "$ZSH_USER_DIR"
        cp "$ZSH_SRC" "$ZSH_USER_DIR/_thinkisp"
        echo -e "  ${GREEN}✔ Autocompletado Zsh copiado a:${RESET} $ZSH_USER_DIR/_thinkisp"
        echo -e "    ${YELLOW}ℹ Para activar Zsh:${RESET} Añade en tu ${CYAN}~/.zshrc${RESET} (antes de compinit):"
        echo -e "      ${CYAN}fpath=(~/.zsh/completion \$fpath)${RESET}"
        echo -e "      ${CYAN}autoload -Uz compinit && compinit${RESET}"
        echo -e "    ${DIM}(O instala globalmente con: sudo cp $ZSH_SRC /usr/share/zsh/site-functions/_thinkisp)${RESET}"
    fi
fi

# Bash completion
BASH_USER_DIR="${HOME}/.local/share/bash-completion/completions"
BASH_SRC="$PROJECT_DIR/extras/completions/thinkisp.bash"
if [ -f "$BASH_SRC" ]; then
    mkdir -p "$BASH_USER_DIR"
    cp "$BASH_SRC" "$BASH_USER_DIR/thinkisp"
    ln -sf "$BASH_USER_DIR/thinkisp" "$BASH_USER_DIR/isp"
    echo -e "  ${GREEN}✔ Autocompletado Bash instalado en:${RESET} $BASH_USER_DIR/thinkisp"
fi

# Fish completion
FISH_USER_DIR="${HOME}/.config/fish/completions"
FISH_SRC="$PROJECT_DIR/extras/completions/thinkisp.fish"
if [ -f "$FISH_SRC" ]; then
    mkdir -p "$FISH_USER_DIR"
    cp "$FISH_SRC" "$FISH_USER_DIR/thinkisp.fish"
    ln -sf "$FISH_USER_DIR/thinkisp.fish" "$FISH_USER_DIR/isp.fish"
    echo -e "  ${GREEN}✔ Autocompletado Fish instalado en:${RESET} $FISH_USER_DIR/thinkisp.fish"
fi

echo -e "\n${BOLD}${GREEN}✔ ¡Instalación completada exitosamente!${RESET}"
echo -e "  ${DIM}Ejecuta ${BOLD}thinkisp${RESET}${DIM} para abrir el Dashboard interactivo (TUI).${RESET}"
echo -e "  ${DIM}O usa comandos directos como ${BOLD}isp on${RESET}${DIM}, ${BOLD}isp boost${RESET}${DIM}, ${BOLD}isp top${RESET}${DIM}, ${BOLD}isp qr${RESET}.${RESET}\n"
