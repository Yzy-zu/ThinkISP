"""
ThinkISP - Configuración Global y Constantes
"""

import os
from pathlib import Path
import subprocess

# Nombres y perfiles de conexión
HOTSPOT_CONN = "sputnik-hotspot"
DEFAULT_SSID = "sputnik"
DEFAULT_PSK = "123456789z"


def detect_wlan_iface():
    """Detecta automáticamente la interfaz Wi-Fi activa en cualquier distro Linux."""
    if os.environ.get("THINKISP_WLAN"):
        return os.environ["THINKISP_WLAN"]
    # 1. Sysfs inspection
    sys_net = Path("/sys/class/net")
    if sys_net.exists():
        for p in sorted(sys_net.iterdir()):
            if (p / "wireless").exists() or (p / "phy80211").exists():
                return p.name
    # 2. NetworkManager query
    try:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "DEVICE,TYPE", "device"],
            text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            dev, _, dtype = line.partition(":")
            if dtype == "wifi":
                return dev
    except Exception:
        pass
    return "wlan0"


def detect_eth_iface(wlan_iface="wlan0"):
    """Detecta automáticamente la interfaz Ethernet/Upstream en cualquier laptop o distro."""
    if os.environ.get("THINKISP_ETH"):
        return os.environ["THINKISP_ETH"]
    # 1. Default route interface (siempre que no sea wifi ni interfaz virtual)
    try:
        out = subprocess.check_output(
            ["ip", "-4", "route", "show", "default"],
            text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            parts = line.split()
            if "dev" in parts:
                idx = parts.index("dev")
                if idx + 1 < len(parts):
                    cand = parts[idx + 1]
                    if cand != wlan_iface and not cand.startswith(
                        ("tailscale", "docker", "virbr", "veth", "lo", "tun", "tap", "br-")
                    ):
                        return cand
    except Exception:
        pass
    # 2. NetworkManager Ethernet
    try:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[1] == "ethernet" and parts[2] == "connected" and parts[0] != wlan_iface:
                return parts[0]
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1] == "ethernet" and parts[0] != wlan_iface:
                return parts[0]
    except Exception:
        pass
    # 3. Sysfs búsqueda de Ethernet física
    sys_net = Path("/sys/class/net")
    if sys_net.exists():
        for p in sorted(sys_net.iterdir()):
            name = p.name
            if name != wlan_iface and not name.startswith(
                ("lo", "tailscale", "docker", "virbr", "veth", "tun", "tap", "br-")
            ):
                if not (p / "wireless").exists() and not (p / "phy80211").exists():
                    return name
    return "eth0"


WLAN_IFACE = detect_wlan_iface()
ETH_IFACE = detect_eth_iface(WLAN_IFACE)

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
