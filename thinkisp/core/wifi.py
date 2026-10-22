"""
ThinkISP - Módulo Wi-Fi y Gestión de Clientes
"""

from pathlib import Path
import re
import shutil
import socket
import subprocess
import time

from thinkisp.config import ap_interface
from thinkisp.config import (
    HOTSPOT_CONN,
    WLAN_IFACE,
    TIMER_FILE,
)
from thinkisp.core.network import (
    format_bytes,
    format_duration,
    get_ethernet_status,
    get_iface_bytes,
    get_current_wifi_connection,
    ThinkISPError,
    is_hotspot_active,
    load_state,
    notify,
    run_cmd,
    save_state,
    state_lock,
)
from thinkisp.core.profile import (
    apply_radio_settings, hotspot_details, inspect_capabilities, require_hotspot_profile, profile_uuid, settings_arguments, ensure_profile_editable,
)
from thinkisp.core.access import apply_saved_blocks, clear_applied_blocks, set_client_block, blocked_clients
from thinkisp.core.dns_state import apply_saved_dns, restore_dns


def get_arp_map():
    """Obtiene el mapeo MAC -> IP de la interfaz Wi-Fi."""
    arp_map = {}
    if not ap_interface(WLAN_IFACE):
        return arp_map
    try:
        with open("/proc/net/arp") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 6 and parts[5] == ap_interface(WLAN_IFACE):
                    arp_map[parts[3].lower()] = parts[0]
    except Exception:
        pass

    neigh_out = run_cmd(["ip", "neigh", "show", "dev", ap_interface(WLAN_IFACE)])
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
        Path(f"/var/lib/NetworkManager/dnsmasq-{ap_interface(WLAN_IFACE)}.leases"),
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


def get_connected_clients(check=False):
    """Obtiene los dispositivos conectados al hotspot con métricas en tiempo real."""
    clients = []
    if not ap_interface(WLAN_IFACE):
        if check:
            raise ThinkISPError("No se detectó un adaptador Wi-Fi.")
        return clients
    iw_out = run_cmd(["iw", "dev", ap_interface(WLAN_IFACE), "station", "dump"], check=check)
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
                    "conn_time": None,
                    "rx_bytes": None,
                    "tx_bytes": None,
                }
                continue
            if current_mac:
                for counter in ("rx_bytes", "tx_bytes"):
                    match = re.search(counter.replace("_", " ") + r":\s+(\d+)", line)
                    if match:
                        stations[current_mac][counter] = int(match[1])
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
            "conn_time": data["conn_time"],
            "rx_bytes": data["rx_bytes"],
            "tx_bytes": data["tx_bytes"],
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
    """Optimización explícita: no se ejecuta al iniciar ni promete latencia cero."""
    if not ap_interface(WLAN_IFACE):
        raise ThinkISPError("No se detectó una interfaz Wi-Fi.")
    run_cmd(["iw", "dev", ap_interface(WLAN_IFACE), "set", "power_save", "off"], check=True)


def start_hotspot():
    from thinkisp.core import connectivity
    owner = profile_uuid()
    state = load_state()
    current = connectivity.journal()
    if current.get('phase', 'idle') != 'idle' and current.get('owner') != owner:
        raise ThinkISPError('Hay una sesión de otro perfil; apágala o recupérala antes de continuar.')
    if not connectivity.has_intent(state, owner):
        return _start_hotspot_legacy()
    with connectivity.operation_lock():
        current = connectivity.journal()
        if is_hotspot_active(check=True):
            if current.get('owner') != owner or current.get('phase') != 'running':
                raise ThinkISPError('El AP está activo fuera del plan guardado. Apágalo antes de aplicar conectividad.')
            return _start_hotspot_legacy()
        if current and current.get('phase') != 'idle':
            connectivity.cleanup(owner)
        if state.get('pending_start'):
            errors = _cleanup_session(owner)
            if errors:
                raise ThinkISPError('; '.join(errors))
        connectivity.prepare(owner)
        try:
            result = _start_hotspot_legacy(announce=False)
            connectivity.confirm(owner)
            notify("thinkISP · Zona Wi-Fi activada", f"Interfaz: {ap_interface(WLAN_IFACE)}", icon="network-wireless-hotspot")
            return result
        except (ThinkISPError, OSError) as exc:
            try:
                if is_hotspot_active(check=True):
                    run_cmd(['nmcli','connection','down','uuid',owner],check=True)
                errors = _cleanup_session(owner)
                if errors:
                    raise ThinkISPError('; '.join(errors))
            except ThinkISPError as recovery:
                raise ThinkISPError(f'{exc}. Recuperación pendiente: {recovery}') from exc
            raise


def _start_hotspot_legacy(announce=True):
    """Preparar controles antes de abrir el AP y conservar recuperación previa."""
    if is_hotspot_active(check=True):
        profile = require_hotspot_profile()
        apply_saved_blocks(profile)
        apply_saved_dns(profile)
        return True, "El punto de acceso ya está activo; controles reconciliados."
    if not ap_interface(WLAN_IFACE):
        raise ThinkISPError("No se detectó un adaptador Wi-Fi.")
    profile = require_hotspot_profile()
    pending = load_state().get('pending_start')
    if pending:
        if pending.get('profile_uuid') != profile:
            raise ThinkISPError('Hay una recuperación pendiente de otro perfil. Selecciónalo y ejecuta thinkisp recover.')
        errors = _cleanup_session(profile)
        if errors:
            raise ThinkISPError('; '.join(errors))
    restore_dns(profile)
    supported = run_cmd(["nmcli", "-g", "WIFI-PROPERTIES.AP", "device", "show", ap_interface(WLAN_IFACE)], check=True)
    if supported != "yes":
        raise ThinkISPError(f"{ap_interface(WLAN_IFACE)} no anuncia soporte para modo AP.")
    previous_wifi = get_current_wifi_connection(check=True)
    with state_lock() as state:
        state['pending_start'] = {'profile_uuid': profile, 'previous_wifi': previous_wifi,
                                  'activation_attempted': False}
        save_state(state)
    attempted = confirmed = False
    try:
        # Ingress filtra desde el primer paquete; no abrir antes de aplicar la lista.
        apply_saved_blocks(profile)
        with state_lock() as state:
            state['pending_start']['activation_attempted'] = True
            save_state(state)
        attempted = True
        run_cmd(["nmcli", "--wait", "60", "connection", "up", "uuid", profile,
                 "ifname", ap_interface(WLAN_IFACE)], check=True, timeout=65)
        confirmed = is_hotspot_active(check=True)
        if not confirmed:
            raise ThinkISPError("NetworkManager no confirmó la activación del hotspot.")
        apply_radio_settings(profile)
        apply_saved_dns(profile)
    except ThinkISPError as exc:
        errors = []
        may_clear_rules = True
        try:
            if confirmed or (attempted and profile in run_cmd(
                    ['nmcli', '-g', 'UUID', 'connection', 'show', '--active'], check=True).splitlines()):
                run_cmd(["nmcli", "connection", "down", "uuid", profile], check=True)
        except ThinkISPError as cleanup:
            errors.append(str(cleanup))
            may_clear_rules = False  # Conservar bloqueos si no se confirmó el cierre.
        errors.extend(_cleanup_session(profile, clear_firewall=may_clear_rules))
        if errors:
            raise ThinkISPError(f"{exc}. Recuperación pendiente: {'; '.join(errors)}") from exc
        raise
    rx, tx = get_iface_bytes(ap_interface(WLAN_IFACE))
    try:
        with state_lock() as state:
            if previous_wifi:
                state["last_wifi"] = previous_wifi
            else:
                state.pop("last_wifi", None)
            state.pop('pending_start', None)
            state["session"] = {"start_time": time.time(), "start_rx": rx,
                                "start_tx": tx, "profile_uuid": profile}
            save_state(state)
    except ThinkISPError as exc:
        raise ThinkISPError(f"El hotspot está encendido, pero no se pudo registrar la sesión: {exc}") from exc
    if announce:
        notify("thinkISP · Zona Wi-Fi activada", f"Interfaz: {ap_interface(WLAN_IFACE)}", icon="network-wireless-hotspot")
    return True, "Zona Wi-Fi activada con éxito."


def stop_hotspot():
    """Detiene la conexión del hotspot y restaura la red anterior."""
    profile = profile_uuid()
    active = is_hotspot_active(check=True)
    if not active:
        TIMER_FILE.unlink(missing_ok=True)
        errors = _cleanup_session(profile)
        if errors:
            raise ThinkISPError('Hotspot inactivo; recuperación pendiente: ' + '; '.join(errors))
        return True, None

    state = load_state()
    session = state.get("session")
    session_summary = None
    if session:
        rx, tx = get_iface_bytes(ap_interface(WLAN_IFACE))
        duration = time.time() - session["start_time"]
        downloaded = max(0, tx - session["start_tx"])
        uploaded = max(0, rx - session["start_rx"])
        session_summary = {
            "duration": duration,
            "downloaded": downloaded,
            "uploaded": uploaded,
            "end_time": time.time()
        }
    run_cmd(["nmcli", "connection", "down", "id", HOTSPOT_CONN], check=True)
    if is_hotspot_active(check=True):
        raise ThinkISPError("El hotspot continúa activo después de solicitar su apagado.")
    TIMER_FILE.unlink(missing_ok=True)
    errors = []
    try:
        with state_lock() as state:
            if session_summary:
                state["last_session"] = session_summary
                state.pop("session", None)
            save_state(state)
    except ThinkISPError as exc:
        errors.append(str(exc))
    errors.extend(_cleanup_session(profile))
    if errors:
        raise ThinkISPError('Hotspot apagado; recuperación pendiente: ' + '; '.join(errors))
    notify("Zona Wi-Fi Apagada", "Punto de acceso desactivado y red previa restaurada.", icon="network-wireless")
    return True, session_summary


def _cleanup_session(profile, clear_firewall=True):
    """Intentar todas las restauraciones aunque falle una; conservar pendientes."""
    errors = []
    with state_lock() as state:
        pending = state.get('pending_start', {})
        if pending.get('profile_uuid') == profile and not pending.get('closing'):
            pending['closing'] = True
            save_state(state)
    operations = [lambda: restore_dns(profile)]
    if profile and clear_firewall:
        operations.append(lambda: clear_applied_blocks(profile))
    for operation in operations:
        try:
            operation()
        except ThinkISPError as exc:
            errors.append(str(exc))
    try:
        from thinkisp.core.connectivity import journal
        managed_session = journal().get('phase', 'idle') != 'idle'
        with state_lock() as state:
            pending = state.get('pending_start', {})
            previous = pending.get('previous_wifi') if pending.get('activation_attempted') else state.get('last_wifi')
            if managed_session:
                previous = None  # UUID restoration is owned by the connectivity journal.
                state.pop('last_wifi', None)
            if previous:
                run_cmd(['nmcli', 'connection', 'up', 'id', previous], check=True, timeout=65)
                state.pop('last_wifi', None)
            if pending and pending.get('profile_uuid') == profile:
                state.pop('pending_start', None)
            if previous or pending:
                save_state(state)
    except ThinkISPError as exc:
        errors.append(str(exc))
    try:
        from thinkisp.core.connectivity import cleanup
        cleanup(profile)
    except ThinkISPError as exc:
        errors.append(str(exc))
    return errors


def recover_network():
    if is_hotspot_active(check=True):
        raise ThinkISPError("Apaga el hotspot antes de recuperar el estado de red.")
    TIMER_FILE.unlink(missing_ok=True)
    errors = _cleanup_session(profile_uuid())
    if errors:
        raise ThinkISPError('; '.join(errors))
    return True, 'DNS y conexión anterior recuperados; preferencias y lista de bloqueos conservadas.'


def kick_client(target):
    """Bloquea el acceso al equipo/router por IP o MAC y conserva la preferencia."""
    return set_client_block(target, True)


def unkick_client(target_mac):
    """Desbloquea una dirección MAC."""
    return set_client_block(target_mac, False)


def get_blocked_clients():
    """Obtiene la lista de direcciones MAC bloqueadas."""
    return blocked_clients()


def get_stealth_mode():
    """Comprueba si el SSID está oculto."""
    hidden_res = run_cmd(["nmcli", "-g", "802-11-wireless.hidden", "connection", "show", HOTSPOT_CONN])
    return (hidden_res == "sí" or hidden_res == "yes")


def set_stealth_mode(enabled):
    """Activa o desactiva la visibilidad del SSID."""
    ensure_profile_editable()
    val = "yes" if enabled else "no"
    run_cmd(["nmcli", "connection", "modify", "id", HOTSPOT_CONN, "802-11-wireless.hidden", val], check=True)
    if is_hotspot_active():
        run_cmd(["nmcli", "connection", "up", "id", HOTSPOT_CONN], check=True, timeout=65)
        apply_radio_settings(require_hotspot_profile())
    return True


def set_band(band):
    """Selecciona una banda disponible; NetworkManager elige el canal."""
    ensure_profile_editable()
    aliases = {"5g": "a", "5": "a", "2.4g": "bg", "2.4": "bg", "auto": ""}
    if band not in aliases:
        raise ThinkISPError("Banda no válida. Usa 'thinkisp setup' para consultar las disponibles.")
    selected = aliases[band]
    caps = inspect_capabilities()
    if selected and selected not in caps.bands:
        raise ThinkISPError("La tarjeta no anuncia canales AP disponibles en esa banda.")
    radio = {"802-11-wireless.band": selected, "802-11-wireless.channel": "0"}
    if caps.optional.get("802-11-wireless.channel-width"):
        radio["802-11-wireless.channel-width"] = "auto"
    args = ["nmcli", "connection", "modify", "id", HOTSPOT_CONN, *settings_arguments(radio)]
    run_cmd(args, check=True)
    if is_hotspot_active():
        run_cmd(["nmcli", "connection", "up", "id", HOTSPOT_CONN], check=True, timeout=65)
        apply_radio_settings(require_hotspot_profile())
    return True, f"Banda configurada: {band}; selección automática del canal."


def optimize_low_latency():
    """Aplica optimizaciones de latencia cero desactivando power save y ampliando txqueuelen."""
    return apply_zero_latency_optimizations()


def wifi_qr_payload(ssid, psk, hidden=False, security="wpa-psk"):
    def escape(value):
        return re.sub(r'([\\;,:\"])', r'\\\1', value)
    auth = "SAE" if security == "sae" else "WPA"
    return f"WIFI:S:{escape(ssid)};T:{auth};P:{escape(psk)};" + ("H:true;" if hidden else "") + ";"


def get_qr_ascii(ssid=None, psk=None):
    """Genera representación del código QR en ASCII / UTF-8 para conexión Wi-Fi rápida."""
    data = hotspot_details(secrets=True) if ssid is None or psk is None else {}
    ssid, psk = data.get("ssid", ssid), data.get("psk", psk)
    wifi_str = wifi_qr_payload(ssid, psk, data.get("hidden", False), data.get("security", "wpa-psk"))
    if shutil.which("qrencode"):
        out = run_cmd(["qrencode", "-t", "UTF8", wifi_str])
        if out:
            return out
    return f"+-----------------------+\n| QR: {ssid} |\n| PSK: {psk} |\n+-----------------------+"


def _serialized_cleanup(operation):
    from functools import wraps
    @wraps(operation)
    def wrapped(*args, **kwargs):
        from thinkisp.core import connectivity
        if connectivity.journal().get('phase', 'idle') != 'idle':
            with connectivity.operation_lock():
                return operation(*args, **kwargs)
        return operation(*args, **kwargs)
    return wrapped


stop_hotspot = _serialized_cleanup(stop_hotspot)
recover_network = _serialized_cleanup(recover_network)
