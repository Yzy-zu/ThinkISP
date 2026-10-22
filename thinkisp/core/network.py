"""
ThinkISP - Módulo de Red del Sistema y NetworkManager
"""

import json
from pathlib import Path
import re
import shutil
import subprocess
import time

from thinkisp.config import (
    DAEMON_PID_FILE,
    ETH_IFACE,
    HOTSPOT_CONN,
    STATE_DIR,
    STATE_FILE,
    TIMER_FILE,
    WLAN_IFACE,
)


def run_cmd(args, check=False, timeout=12):
    """Ejecuta un comando del sistema de forma segura."""
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if check and res.returncode != 0:
            return None
        return res.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
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
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def save_state(state):
    """Guarda el archivo de estado persistente."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


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
    m = re.search(r"([0-9.]+)", s)
    if not m:
        return None
    val = float(m.group(1))
    if "k" in s:
        val /= 1000.0
    elif "g" in s:
        val *= 1000.0
    return val


def get_iface_bytes(iface):
    """Lee bytes recibidos y transmitidos de la interfaz desde sysfs."""
    rx_path = Path(f"/sys/class/net/{iface}/statistics/rx_bytes")
    tx_path = Path(f"/sys/class/net/{iface}/statistics/tx_bytes")
    rx = int(rx_path.read_text().strip()) if rx_path.exists() else 0
    tx = int(tx_path.read_text().strip()) if tx_path.exists() else 0
    return rx, tx


def is_hotspot_active():
    """Comprueba si el hotspot de NetworkManager está actualmente activo."""
    out = run_cmd(["nmcli", "-t", "-f", "NAME,DEVICE,STATE", "connection", "show", "--active"])
    if not out:
        return False
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] == HOTSPOT_CONN and parts[2] == "activated":
            return True
    return False


def get_current_wifi_connection():
    """Obtiene el nombre de la conexión Wi-Fi cliente activa antes de activar el hotspot."""
    out = run_cmd(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE,STATE", "connection", "show", "--active"])
    if not out:
        return None
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 4 and parts[1] == "802-11-wireless" and parts[0] != HOTSPOT_CONN:
            return parts[0]
    return None


def get_upstream_connections():
    """Obtiene las conexiones cableadas o de salida para propagar DNS."""
    conns = []
    out = run_cmd(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    if out:
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1] == "802-3-ethernet":
                conns.append(parts[0])
    if not conns:
        conns.append("Conexión cableada 1")
    return conns


def apply_upstream_dns(dns_servers=None):
    """Aplica servidores DNS upstream en conexiones Ethernet. Si es None, restablece a DHCP."""
    conns = get_upstream_connections()
    for conn in conns:
        if dns_servers:
            run_cmd(["nmcli", "connection", "modify", conn, "ipv4.dns", dns_servers, "ipv4.ignore-auto-dns", "yes"])
        else:
            run_cmd(["nmcli", "connection", "modify", conn, "ipv4.dns", "", "ipv4.ignore-auto-dns", "no"])


def get_ethernet_status():
    """Comprueba el estado del cable de red Ethernet (escuela o laboratorio)."""
    carrier_path = Path(f"/sys/class/net/{ETH_IFACE}/carrier")
    operstate_path = Path(f"/sys/class/net/{ETH_IFACE}/operstate")
    
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
    if has_carrier:
        eth_out = run_cmd(["ethtool", ETH_IFACE])
        if eth_out:
            m = re.search(r"Speed:\s*([^\n]+)", eth_out)
            if m:
                speed = m.group(1)

    return {
        "interface": ETH_IFACE,
        "connected": has_carrier,
        "operstate": operstate,
        "speed": speed
    }


def get_hotspot_ip():
    """Obtiene la dirección IP local de la interfaz Wi-Fi compartida."""
    out = run_cmd(["ip", "-4", "-o", "addr", "show", "dev", WLAN_IFACE])
    if out:
        m = re.search(r"inet\s+([0-9.]+/\d+)", out)
        if m:
            return m.group(1)
    return "10.42.0.1/24"


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
    return res is not None


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


def get_interface_stats(iface=WLAN_IFACE):
    """Obtiene estadísticas detalladas de transmisión/recepción de una interfaz."""
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
