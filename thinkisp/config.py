"""
ThinkISP - Configuración Global y Constantes
"""

import os
from pathlib import Path

# Nombres y perfiles de conexión
HOTSPOT_CONN = "sputnik-hotspot"
DEFAULT_SSID = "sputnik"
DEFAULT_PSK = "123456789z"
WLAN_IFACE = "wlan0"
ETH_IFACE = "enp0s31f6"

# Directorios de estado y datos
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "isp"
STATE_FILE = STATE_DIR / "state.json"
TIMER_FILE = STATE_DIR / "timer.json"
DAEMON_PID_FILE = STATE_DIR / "daemon.pid"

# Rutas de binarios del sistema
THINKPAD_MODE_BIN = Path.home() / ".local/bin/thinkpad-mode"

# Proveedores de DNS
DNS_PROVIDERS = {
    "cloudflare": {
        "name": "Cloudflare DNS",
        "ip_test": "1.1.1.1",
        "servers": "1.1.1.1 1.0.0.1",
        "desc": "Ultra-baja latencia y privacidad"
    },
    "google": {
        "name": "Google Public DNS",
        "ip_test": "8.8.8.8",
        "servers": "8.8.8.8 8.8.4.4",
        "desc": "Máxima estabilidad y velocidad global"
    },
    "quad9": {
        "name": "Quad9 DNS Seguro",
        "ip_test": "9.9.9.9",
        "servers": "9.9.9.9 149.112.112.112",
        "desc": "Bloqueo de malware y privacidad suiza"
    },
    "adguard": {
        "name": "AdGuard DNS (Anti-Anuncios)",
        "ip_test": "94.140.14.14",
        "servers": "94.140.14.14 94.140.15.15",
        "desc": "Bloquea publicidad y rastreadores en toda la red"
    },
    "opendns": {
        "name": "Cisco OpenDNS",
        "ip_test": "208.67.222.222",
        "servers": "208.67.222.222 208.67.220.220",
        "desc": "Filtrado inteligente y protección web"
    }
}

# Colores ANSI para CLI
BOLD = "\033[1m"
GREEN = "\033[1;32m"
BLUE = "\033[1;34m"
CYAN = "\033[1;36m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
MAGENTA = "\033[1;35m"
DIM = "\033[2m"
RESET = "\033[0m"
