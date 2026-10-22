"""
ThinkISP - Módulo Wi-Fi y Gestión de Clientes
"""

from pathlib import Path
import re
import shutil
import socket
import subprocess
import time

from thinkisp.config import (
    DEFAULT_PSK,
    DEFAULT_SSID,
    HOTSPOT_CONN,
    THINKPAD_MODE_BIN,
    WLAN_IFACE,
)
from thinkisp.core.network import (
    format_bytes,
    format_duration,
    get_ethernet_status,
    get_iface_bytes,
    is_hotspot_active,
    load_state,
    notify,
    run_cmd,
    save_state,
)


def get_arp_map():
    """Obtiene el mapeo MAC -> IP de la interfaz Wi-Fi."""
    arp_map = {}
    try:
        with open("/proc/net/arp") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 6 and parts[5] == WLAN_IFACE:
                    arp_map[parts[3].lower()] = parts[0]
    except Exception:
        pass

    neigh_out = run_cmd(["ip", "neigh", "show", "dev", WLAN_IFACE])
    if neigh_out:
        for line in neigh_out.splitlines():
            parts = line.split()
            if "lladdr" in parts:
                idx = parts.index("lladdr")
                if idx + 1 < len(parts):
                    arp_map[parts[idx + 1].lower()] = parts[0]
    return arp_map


def get_hostname_for_ip(ip):
    """Resuelve el nombre de host de una dirección IP de forma segura."""
    if not ip or ip == "Desconocida":
        return "Dispositivo móvil"
    try:
        host_info = socket.gethostbyaddr(ip)
        if host_info and host_info[0]:
            return host_info[0]
    except Exception:
        pass
    return "Dispositivo móvil"


def parse_dhcp_leases(path=None):
    """Parsea el archivo de asignaciones DHCP (leases) de dnsmasq / NetworkManager."""
    lease_paths = [Path(path)] if path else [
        Path(f"/var/lib/NetworkManager/dnsmasq-{WLAN_IFACE}.leases"),
        Path("/var/lib/NetworkManager/dnsmasq.leases"),
        Path("/var/lib/misc/dnsmasq.leases"),
    ]
    leases = {}
    for lp in lease_paths:
        if lp and lp.exists():
            try:
                for line in lp.read_text().splitlines():
                    parts = line.split()
                    if len(parts) >= 4:
                        mac = parts[1].lower()
                        ip = parts[2]
                        host = parts[3] if parts[3] != "*" else "Dispositivo móvil"
                        leases[mac] = {"ip": ip, "hostname": host}
                break
            except Exception:
                pass
    return leases


def get_connected_clients():
    """Obtiene los dispositivos conectados al hotspot con métricas en tiempo real."""
    clients = []
    iw_out = run_cmd(["iw", "dev", WLAN_IFACE, "station", "dump"])
    stations = {}
    if iw_out:
        current_mac = None
        for line in iw_out.splitlines():
            m = re.match(r"Station\s+([0-9a-fA-F:]{17})", line)
            if m:
                current_mac = m.group(1).lower()
                stations[current_mac] = {
                    "mac": current_mac,
                    "signal": None,
                    "tx_mbps": None,
                    "rx_mbps": None,
                    "conn_time": None
                }
                continue
            if current_mac:
                sig = re.search(r"signal:\s+(-?\d+)", line)
                if sig:
                    stations[current_mac]["signal"] = int(sig.group(1))
                tx = re.search(r"tx bitrate:\s+([0-9.]+)\s*MBit", line)
                if tx:
                    stations[current_mac]["tx_mbps"] = float(tx.group(1))
                rx = re.search(r"rx bitrate:\s+([0-9.]+)\s*MBit", line)
                if rx:
                    stations[current_mac]["rx_mbps"] = float(rx.group(1))
                ct = re.search(r"connected time:\s+(\d+)", line)
                if ct:
                    stations[current_mac]["conn_time"] = int(ct.group(1))

    arp_map = get_arp_map()
    leases = parse_dhcp_leases()

    for mac, data in stations.items():
        ip = arp_map.get(mac)
        hostname = None
        if mac in leases:
            if not ip or ip == "Desconocida":
                ip = leases[mac]["ip"]
            hostname = leases[mac]["hostname"]

        if not ip:
            ip = "Desconocida"
        if not hostname or hostname == "Dispositivo móvil":
            hostname = get_hostname_for_ip(ip)

        clients.append({
            "mac": mac,
            "ip": ip,
            "hostname": hostname,
            "signal": data["signal"],
            "tx_mbps": data["tx_mbps"],
            "rx_mbps": data["rx_mbps"],
            "conn_time": data["conn_time"]
        })

    return clients


def format_signal_bar(dbm):
    """Devuelve una representación gráfica de la señal Wi-Fi."""
    if dbm is None:
        return "N/D"
    if dbm >= -50:
        return f"{dbm} dBm [████] (Excelente)"
    elif dbm >= -65:
        return f"{dbm} dBm [███░] (Muy Buena)"
    elif dbm >= -75:
        return f"{dbm} dBm [██░░] (Aceptable)"
    else:
        return f"{dbm} dBm [█░░░] (Débil)"


def apply_zero_latency_optimizations():
    """Desactiva Power Save en la tarjeta Wi-Fi y optimiza buffers de transmisión."""
    run_cmd(["iw", "dev", WLAN_IFACE, "set", "power_save", "off"])
    run_cmd(["ip", "link", "set", "dev", WLAN_IFACE, "txqueuelen", "2000"])


def start_hotspot():
    """Inicia la conexión del hotspot y optimiza latencia."""
    if is_hotspot_active():
        return True, "El punto de acceso ya está activo."

    up_res = run_cmd(["nmcli", "connection", "up", HOTSPOT_CONN])
    if not is_hotspot_active():
        return False, f"Error al activar la conexión: {up_res}"

    apply_zero_latency_optimizations()

    # Registrar estadísticas de inicio de sesión
    rx, tx = get_iface_bytes(WLAN_IFACE)
    state = load_state()
    state["session"] = {
        "start_time": time.time(),
        "start_rx": rx,
        "start_tx": tx
    }
    save_state(state)

    notify(
        "🚀 Zona Wi-Fi 'sputnik' Activada",
        f"SSID: {DEFAULT_SSID}\nClave: {DEFAULT_PSK}\n5GHz 80MHz | 0 Latencia",
        icon="network-wireless-hotspot"
    )
    return True, "Zona Wi-Fi activada con éxito."


def stop_hotspot():
    """Detiene la conexión del hotspot y restaura la red anterior."""
    if not is_hotspot_active():
        return True, "La zona Wi-Fi ya estaba inactiva."

    state = load_state()
    session = state.get("session")
    session_summary = None
    if session:
        rx, tx = get_iface_bytes(WLAN_IFACE)
        duration = time.time() - session["start_time"]
        downloaded = max(0, tx - session["start_tx"])
        uploaded = max(0, rx - session["start_rx"])
        session_summary = {
            "duration": duration,
            "downloaded": downloaded,
            "uploaded": uploaded,
            "end_time": time.time()
        }
        state["last_session"] = session_summary
        state.pop("session", None)

    run_cmd(["nmcli", "connection", "down", HOTSPOT_CONN])

    # Restaurar perfil ThinkPad si se usó
    prev_tp = state.get("prev_tp_mode")
    if prev_tp and THINKPAD_MODE_BIN.exists():
        try:
            run_cmd([str(THINKPAD_MODE_BIN), prev_tp])
            state.pop("prev_tp_mode", None)
        except Exception:
            pass

    # Reconectar Wi-Fi previo
    last_wifi = state.get("last_wifi")
    if last_wifi:
        run_cmd(["nmcli", "connection", "up", last_wifi])
        state.pop("last_wifi", None)

    save_state(state)
    notify("Zona Wi-Fi Apagada", "Punto de acceso desactivado y red previa restaurada.", icon="network-wireless")
    return True, session_summary


def kick_client(target):
    """Expulsa y bloquea permanentemente a un dispositivo por IP o MAC."""
    target = target.strip()
    target_mac = None

    if re.match(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$", target):
        target_mac = target.lower()
    else:
        arp_map = get_arp_map()
        for mac, ip in arp_map.items():
            if ip == target:
                target_mac = mac.lower()
                break

    if not target_mac:
        return False, f"No se encontró ningún dispositivo con IP o MAC '{target}'."

    run_cmd(["nmcli", "connection", "modify", HOTSPOT_CONN, "+802-11-wireless.mac-address-denylist", target_mac])
    if is_hotspot_active():
        run_cmd(["nmcli", "connection", "up", HOTSPOT_CONN])
        apply_zero_latency_optimizations()

    notify("🚫 Dispositivo Expulsado", f"Se bloqueó el acceso a: {target}\nMAC: {target_mac}", icon="network-wireless")
    return True, f"Dispositivo {target_mac} ({target}) expulsado y bloqueado."


def unkick_client(target_mac):
    """Desbloquea una dirección MAC."""
    target_mac = target_mac.strip().lower()
    run_cmd(["nmcli", "connection", "modify", HOTSPOT_CONN, "-802-11-wireless.mac-address-denylist", target_mac])
    if is_hotspot_active():
        run_cmd(["nmcli", "connection", "up", HOTSPOT_CONN])
        apply_zero_latency_optimizations()
    return True, f"Dispositivo {target_mac} desbloqueado con éxito."


def get_blocked_clients():
    """Obtiene la lista de direcciones MAC bloqueadas."""
    out = run_cmd(["nmcli", "-g", "802-11-wireless.mac-address-denylist", "connection", "show", HOTSPOT_CONN])
    if not out:
        return []
    return [m.strip() for m in re.split(r"[,;\s]+", out) if m.strip()]


def get_stealth_mode():
    """Comprueba si el SSID está oculto."""
    hidden_res = run_cmd(["nmcli", "-g", "802-11-wireless.hidden", "connection", "show", HOTSPOT_CONN])
    return (hidden_res == "sí" or hidden_res == "yes")


def set_stealth_mode(enabled):
    """Activa o desactiva la visibilidad del SSID."""
    val = "yes" if enabled else "no"
    run_cmd(["nmcli", "connection", "modify", HOTSPOT_CONN, "802-11-wireless.hidden", val])
    if is_hotspot_active():
        run_cmd(["nmcli", "connection", "up", HOTSPOT_CONN])
        apply_zero_latency_optimizations()
    return True


def set_band(band):
    """Configura la banda en 5G (Canal 149), 2.4G (Canal 6) o Auto."""
    band = band.lower()
    if band in ("5g", "5ghz", "5"):
        run_cmd([
            "nmcli", "connection", "modify", HOTSPOT_CONN,
            "802-11-wireless.band", "a",
            "802-11-wireless.channel", "149"
        ])
    elif band in ("2.4g", "2.4ghz", "2.4", "2"):
        run_cmd([
            "nmcli", "connection", "modify", HOTSPOT_CONN,
            "802-11-wireless.band", "bg",
            "802-11-wireless.channel", "6"
        ])
    elif band in ("auto", "default", "any"):
        run_cmd([
            "nmcli", "connection", "modify", HOTSPOT_CONN,
            "802-11-wireless.band", "",
            "802-11-wireless.channel", "0"
        ])
    else:
        return False, "Banda no válida. Usa 5g, 2.4g o auto."

    if is_hotspot_active():
        run_cmd(["nmcli", "connection", "up", HOTSPOT_CONN])
        apply_zero_latency_optimizations()
    return True, f"Banda cambiada a {band}."


def optimize_low_latency():
    """Aplica optimizaciones de latencia cero desactivando power save y ampliando txqueuelen."""
    return apply_zero_latency_optimizations()


def get_qr_ascii(ssid=DEFAULT_SSID, psk=DEFAULT_PSK):
    """Genera representación del código QR en ASCII / UTF-8 para conexión Wi-Fi rápida."""
    wifi_str = f"WIFI:S:{ssid};T:WPA;P:{psk};;"
    if shutil.which("qrencode"):
        out = run_cmd(["qrencode", "-t", "UTF8", wifi_str])
        if out:
            return out
    return f"+-----------------------+\n| QR: {ssid} |\n| PSK: {psk} |\n+-----------------------+"
