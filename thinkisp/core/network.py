"""
ThinkISP - Módulo de Red del Sistema y NetworkManager
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import tempfile
import fcntl
from contextlib import contextmanager
from thinkisp.parsing import split_nmcli
from thinkisp.errors import ThinkISPError

from thinkisp.config import ap_interface, upstream_interface
from thinkisp.config import (
    DAEMON_PID_FILE,
    ETH_IFACE,
    HOTSPOT_CONN,
    STATE_DIR,
    STATE_FILE,
    TIMER_FILE,
    WLAN_IFACE,
    detect_eth_iface,
)


class CommandError(ThinkISPError):
    def __init__(self, args, detail, returncode=None):
        # Los errores de nmcli pueden repetir un argumento: nunca revelar PSK.
        args = [str(arg) for arg in args]
        for index, arg in enumerate(args[:-1]):
            if args[index + 1] and any(name in arg for name in ("password", "psk", "wep-key")):
                detail = detail.replace(args[index + 1], "[oculto]")
        self.returncode = returncode
        self.detail = detail
        super().__init__(f"{args[0]}: {detail}")


def run_cmd(args, check=False, timeout=12, input_text=None, strip=True):
    """Las consultas opcionales devuelven None al fallar; check exige éxito."""
    try:
        kwargs = {"input": input_text} if input_text is not None else {}
        res = subprocess.run(
            args, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
            env={**os.environ, "LC_ALL": "C"}, **kwargs,
        )
        if res.returncode != 0:
            if check:
                raise CommandError(args, res.stderr.strip() or res.stdout.strip()
                                   or f"salida {res.returncode}", res.returncode)
            return None
        return res.stdout.strip() if strip else res.stdout.rstrip("\n")
    except subprocess.TimeoutExpired as exc:
        if check:
            raise CommandError(args, f"tiempo de espera agotado ({timeout} s)") from exc
        return None
    except OSError as exc:
        if check:
            raise CommandError(args, str(exc)) from exc
        return None


def notify(title, message, icon="network-wireless-hotspot", urgency="normal"):
    """Envía notificación al escritorio (Hyprland / mako / dunst)."""
    if shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "-a", "ThinkISP", "-i", icon, "-u", urgency, title, message],
                timeout=4,
                capture_output=True
            )
        except Exception:
            pass


def load_state():
    """Carga el archivo de estado persistente."""
    try:
        state = json.loads(STATE_FILE.read_text())
        if not isinstance(state, dict):
            raise ValueError("se esperaba un objeto JSON")
        return state
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ThinkISPError(f"No se pudo leer el estado en {STATE_FILE}: {exc}") from exc


def save_state(state):
    """Guarda el archivo de estado persistente."""
    temp_path = None
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=STATE_DIR, delete=False) as stream:
            temp_path = Path(stream.name)
            json.dump(state, stream, indent=2)
        os.replace(temp_path, STATE_FILE)
    except (OSError, TypeError, ValueError) as exc:
        raise ThinkISPError(f"No se pudo guardar el estado en {STATE_FILE}: {exc}") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@contextmanager
def state_lock():
    """Serializa operaciones de escritura entre CLI, dashboard y vigilante."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / "state.lock").open("a") as stream:
        os.chmod(stream.name, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield load_state()
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def format_bytes(b):
    """Formatea bytes a KB, MB o GB con 2 decimales."""
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    else:
        return f"{b / (1024 * 1024 * 1024):.2f} GB"


def format_duration(seconds):
    """Formatea segundos en formato legible (ej. 1h 24m 10s)."""
    seconds = int(max(0, seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    elif minutes > 0:
        return f"{minutes}m {secs:02d}s"
    else:
        return f"{secs}s"


def parse_duration_str(s):
    """Convierte cadenas como 45m, 1h, 90, 1h30m a segundos."""
    s = s.strip().lower()
    total_sec = 0
    m_hour = re.search(r'(\d+)\s*h', s)
    if m_hour:
        total_sec += int(m_hour.group(1)) * 3600
        s = re.sub(r'\d+\s*h', '', s)
    m_min = re.search(r'(\d+)\s*m', s)
    if m_min:
        total_sec += int(m_min.group(1)) * 60
        s = re.sub(r'\d+\s*m', '', s)
    m_sec = re.search(r'(\d+)\s*s', s)
    if m_sec:
        total_sec += int(m_sec.group(1))
        s = re.sub(r'\d+\s*s', '', s)
    s = s.strip()
    if s.isdigit():
        total_sec += int(s) * 60
    return total_sec


def parse_speed_mbps(s):
    """Convierte cadenas como 30m, 50mbps, 100, 500k a Mbps float."""
    s = s.strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(k|m|g|kbps|mbps|gbps)?", s)
    if not m:
        return None
    val = float(m.group(1))
    if (m.group(2) or "").startswith("k"):
        val /= 1000.0
    elif (m.group(2) or "").startswith("g"):
        val *= 1000.0
    return val


def get_iface_bytes(iface):
    """Lee bytes recibidos y transmitidos de la interfaz desde sysfs."""
    if not iface:
        return 0, 0
    rx_path = Path(f"/sys/class/net/{iface}/statistics/rx_bytes")
    tx_path = Path(f"/sys/class/net/{iface}/statistics/tx_bytes")
    rx = int(rx_path.read_text().strip()) if rx_path.exists() else 0
    tx = int(tx_path.read_text().strip()) if tx_path.exists() else 0
    return rx, tx


def is_hotspot_active(check=False):
    """Comprueba si el hotspot de NetworkManager está actualmente activo."""
    out = run_cmd(["nmcli", "-t", "-f", "NAME,DEVICE,STATE", "connection", "show", "--active"], check=check)
    if not out:
        return False
    for line in out.splitlines():
        parts = split_nmcli(line)
        if (len(parts) >= 3 and parts[0] == HOTSPOT_CONN
                and parts[1] == ap_interface(WLAN_IFACE) and parts[2] == "activated"):
            return True
    return False


def get_current_wifi_connection(check=False):
    """Obtiene el nombre de la conexión Wi-Fi cliente activa antes de activar el hotspot."""
    out = run_cmd(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE,STATE", "connection", "show", "--active"], check=check)
    if not out:
        return None
    for line in out.splitlines():
        parts = split_nmcli(line)
        if (len(parts) >= 4 and parts[1] in ("802-11-wireless", "wifi")
                and parts[2] == ap_interface(WLAN_IFACE) and parts[0] != HOTSPOT_CONN):
            return parts[0]
    return None


def get_upstream_connections():
    """Obtiene sólo los perfiles activos en la salida detectada del equipo."""
    conns = []
    out = run_cmd(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"], check=True)
    if out:
        for line in out.splitlines():
            parts = split_nmcli(line)
            if len(parts) >= 3 and parts[2] == upstream_interface(ETH_IFACE) and parts[0] != HOTSPOT_CONN:
                conns.append(parts[0])
    return conns


def apply_upstream_dns(dns_servers=None):
    """Compatibilidad con la API anterior, usando ahora la sesión recuperable."""
    from thinkisp.config import DNS_PROVIDERS
    from thinkisp.core.dns_state import configure_dns
    if dns_servers is None:
        return configure_dns(None)
    values = set(dns_servers.replace(',', ' ').split())
    for key, provider in DNS_PROVIDERS.items():
        if values == set(provider['servers'].split()):
            return configure_dns(key)
    raise ThinkISPError('Proveedor no registrado; configura los DNS personalizados en el panel técnico.')


def get_ethernet_status():
    """Consulta la salida actual, incluso si cambió desde que abrió el panel."""
    interface = upstream_interface(detect_eth_iface(ap_interface(WLAN_IFACE)))
    if not interface:
        return {'interface': None, 'connected': False, 'operstate': 'Sin salida', 'speed': 'Desconocida'}
    carrier_path = Path(f"/sys/class/net/{interface}/carrier")
    operstate_path = Path(f"/sys/class/net/{interface}/operstate")
    
    has_carrier = False
    if carrier_path.exists():
        try:
            has_carrier = (carrier_path.read_text().strip() == "1")
        except Exception:
            pass
            
    operstate = "desconocido"
    if operstate_path.exists():
        try:
            operstate = operstate_path.read_text().strip()
        except Exception:
            pass

    speed = "Desconocida"
    connected = has_carrier or operstate == 'up'
    if connected:
        eth_out = run_cmd(["ethtool", interface])
        if eth_out:
            m = re.search(r"Speed:\s*([^\n]+)", eth_out)
            if m:
                speed = m.group(1)

    return {
        "interface": interface,
        "connected": connected,
        "operstate": operstate,
        "speed": speed
    }


def get_hotspot_ip():
    """Obtiene la dirección IP local de la interfaz Wi-Fi compartida."""
    if not ap_interface(WLAN_IFACE):
        return "Sin datos"
    out = run_cmd(["ip", "-4", "-o", "addr", "show", "dev", ap_interface(WLAN_IFACE)])
    if out:
        m = re.search(r"inet\s+([0-9.]+/\d+)", out)
        if m:
            return m.group(1)
    return "Sin datos"


def format_speed(bps):
    """Formatea velocidad en bps, Kbps, Mbps o Gbps con decimales legibles."""
    try:
        val = float(bps)
    except (ValueError, TypeError):
        return "0 bps"
    if val < 1000:
        return f"{val:.0f} bps"
    elif val < 1_000_000:
        return f"{val / 1000:.1f} Kbps"
    elif val < 1_000_000_000:
        return f"{val / 1_000_000:.1f} Mbps"
    else:
        return f"{val / 1_000_000_000:.2f} Gbps"


def get_ip_forward_status():
    """Verifica si el enrutamiento de paquetes IPv4 está activo en el kernel."""
    fwd_path = Path("/proc/sys/net/ipv4/ip_forward")
    if fwd_path.exists():
        try:
            return fwd_path.read_text().strip() == "1"
        except Exception:
            pass
    return False


def is_ip_forwarding_enabled():
    """Verifica si el reenvío de paquetes IPv4 está activo en el kernel."""
    return get_ip_forward_status()


def enable_ip_forwarding():
    """Habilita el reenvío de paquetes IPv4 en el sistema."""
    res = run_cmd(["sysctl", "-w", "net.ipv4.ip_forward=1"])
    if res and "net.ipv4.ip_forward = 1" in res:
        return True
    fwd_path = Path("/proc/sys/net/ipv4/ip_forward")
    try:
        fwd_path.write_text("1")
        return True
    except Exception:
        pass
    return False


def get_default_gateway():
    """Obtiene la dirección IP de la puerta de enlace predeterminada."""
    out = run_cmd(["ip", "route", "show", "default"])
    if out:
        m = re.search(r"default via ([0-9.]+)", out)
        if m:
            return m.group(1)
    return None


def get_hotspot_status():
    """Devuelve un resumen del estado del punto de acceso."""
    active = is_hotspot_active()
    return {
        "active": active,
        "ssid": HOTSPOT_CONN if active else None,
        "ip": get_hotspot_ip() if active else None,
        "forwarding": get_ip_forward_status(),
        "ethernet": get_ethernet_status()
    }


def get_interface_stats(iface=None):
    """Obtiene estadísticas detalladas de transmisión/recepción de una interfaz."""
    iface = iface or ap_interface(WLAN_IFACE)
    rx_bytes, tx_bytes = get_iface_bytes(iface)
    rx_pkts_path = Path(f"/sys/class/net/{iface}/statistics/rx_packets")
    tx_pkts_path = Path(f"/sys/class/net/{iface}/statistics/tx_packets")
    rx_pkts = int(rx_pkts_path.read_text().strip()) if rx_pkts_path.exists() else 0
    tx_pkts = int(tx_pkts_path.read_text().strip()) if tx_pkts_path.exists() else 0
    return {
        "interface": iface,
        "rx_bytes": rx_bytes,
        "tx_bytes": tx_bytes,
        "rx_packets": rx_pkts,
        "tx_packets": tx_pkts,
        "rx_formatted": format_bytes(rx_bytes),
        "tx_formatted": format_bytes(tx_bytes),
    }
