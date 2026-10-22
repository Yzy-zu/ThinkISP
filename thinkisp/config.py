"""
ThinkISP - Configuración Global y Constantes
"""

import os
import json
from pathlib import Path
import subprocess
import socket
from thinkisp.parsing import split_nmcli

# Nombres y perfiles de conexión
PROJECT_NAME = "thinkISP"
HOTSPOT_CONN = os.environ.get("THINKISP_PROFILE", PROJECT_NAME)


# Directorios de estado y datos
_state_home = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
if not _state_home.is_absolute():
    _state_home = Path.home() / '.local/state'
STATE_DIR = _state_home / "isp"
STATE_FILE = STATE_DIR / "state.json"
TIMER_FILE = STATE_DIR / "timer.json"
DAEMON_PID_FILE = STATE_DIR / "daemon.pid"


def default_ssid():
    """Nombre inicial por equipo; los perfiles existentes conservan su SSID."""
    hostname = socket.gethostname().split(".")[0]
    name = os.environ.get("THINKISP_SSID", f"{PROJECT_NAME}-{hostname}")
    return name.encode("utf-8")[:32].decode("utf-8", errors="ignore")


def runtime_selection():
    """Read the current process-independent session without querying the network."""
    from thinkisp.errors import ThinkISPError
    try:
        data = json.loads(STATE_FILE.read_text()).get('connectivity_runtime', {})
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, AttributeError) as exc:
        raise ThinkISPError(f'No se pudo leer la sesión de conectividad: {exc}') from exc
    if not isinstance(data, dict):
        raise ThinkISPError('El registro de conectividad está dañado; ejecuta la recuperación.')
    return data if data.get('owner_name') == HOTSPOT_CONN else {}


def ap_interface(fallback=None):
    record = runtime_selection()
    return record.get('ap_interface') or fallback


def upstream_interface(fallback=None):
    record = runtime_selection()
    return record.get('upstream_interface') if record.get('phase') != 'idle' and record.get('upstream_interface') else fallback


def _probe(args):
    try:
        return subprocess.check_output(
            args, text=True, encoding='utf-8', errors='replace', stderr=subprocess.DEVNULL, timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def detect_wlan_iface():
    """Detecta automáticamente la interfaz Wi-Fi activa en cualquier distro Linux."""
    if os.environ.get("THINKISP_WLAN"):
        return os.environ["THINKISP_WLAN"]
    # Preferir un adaptador que NetworkManager pueda utilizar como AP.
    devices = _probe(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"])
    wifi = [parts[0] for line in devices.splitlines()
            for parts in [split_nmcli(line)] if len(parts) == 2 and parts[1] == 'wifi']
    bound = _probe(["nmcli", "-g", "connection.interface-name", "connection", "show", "id", HOTSPOT_CONN])
    if bound in wifi:
        return bound
    for iface in wifi:
        if _probe(['nmcli', '-g', 'GENERAL.NM-MANAGED', 'device', 'show', iface]) == 'no':
            continue
        if _probe(["nmcli", "-g", "WIFI-PROPERTIES.AP", "device", "show", iface]) == "yes":
            return iface
    if wifi:
        return wifi[0]
    # Sysfs permite informar sobre el adaptador aunque NetworkManager no responda.
    sys_net = Path("/sys/class/net")
    if sys_net.exists():
        for p in sorted(sys_net.iterdir()):
            if (p / "wireless").exists() or (p / "phy80211").exists():
                return p.name
    return None


def detect_eth_iface(wlan_iface=None):
    """Salida por ruta de menor métrica; admite USB, bridges y nombres personalizados."""
    if os.environ.get("THINKISP_ETH"):
        return os.environ["THINKISP_ETH"]
    # Preferir IPv4 para la conexión compartida IPv4; admitir también salida IPv6.
    for family in ('-4', '-6'):
        out = _probe(['ip', '-j', family, 'route', 'show', 'default'])
        try:
            routes = json.loads(out)
            candidates = []
            for route in routes:
                if route.get('type', 'unicast') != 'unicast':
                    continue
                devices = [route] if route.get('dev') else route.get('nexthops', [])
                for device in devices:
                    iface = device.get('dev')
                    if iface and iface not in (wlan_iface, 'lo'):
                        candidates.append((int(route.get('metric', 0)), iface))
            if candidates:
                return min(candidates, key=lambda item: item[0])[1]
        except (ValueError, TypeError, AttributeError):
            pass
        # iproute2 antiguo o sin JSON: seguir respetando las métricas.
        candidates = []
        for line in _probe(['ip', family, 'route', 'show', 'default']).splitlines():
            parts = line.split()
            if not parts or parts[0] != 'default' or 'dev' not in parts:
                continue
            try:
                iface = parts[parts.index('dev') + 1]
                metric = int(parts[parts.index('metric') + 1]) if 'metric' in parts else 0
            except (IndexError, ValueError):
                continue
            if iface not in (wlan_iface, 'lo'):
                candidates.append((metric, iface))
        if candidates:
            return min(candidates, key=lambda item: item[0])[1]
    # Sin ruta: sólo considerar conexiones que NM confirme activas.
    out = _probe(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"])
    for line in out.splitlines():
        parts = split_nmcli(line)
        if len(parts) >= 3 and parts[0] not in (wlan_iface, 'lo'):
            if parts[2] == 'connected' and parts[1] not in ('loopback', 'wifi-p2p'):
                return parts[0]
    return None


WLAN_IFACE = detect_wlan_iface()
ETH_IFACE = detect_eth_iface(WLAN_IFACE)

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
