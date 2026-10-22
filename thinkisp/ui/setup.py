"""Panel de configuración reutilizable desde CLI y dashboard."""

import getpass
import socket
import sys
import re

from thinkisp.config import HOTSPOT_CONN, default_ssid
from thinkisp.core.network import CommandError, ThinkISPError, is_hotspot_active, run_cmd
from thinkisp.core.profile import (
    apply_radio_settings, inspect_capabilities, profile_uuid, profile_values,
    save_profile, validate_settings,
)


BASE_FIELDS = {
    "802-11-wireless.ssid": "",
    "802-11-wireless-security.key-mgmt": "wpa-psk",
    "802-11-wireless-security.proto": "rsn",
    "802-11-wireless-security.psk": "",
    "802-11-wireless.band": "",
    "802-11-wireless.channel": "0",
    "802-11-wireless.hidden": "no",
    "802-11-wireless.powersave": "0",
    "802-11-wireless.mtu": "0",
    "ipv4.addresses": "",
}


def _password():
    first = getpass.getpass("Contraseña: ")
    if first != getpass.getpass("Repite la contraseña: "):
        raise ThinkISPError("Las contraseñas no coinciden.")
    return first


def _pick(title, choices):
    print(title)
    for index, (value, label) in enumerate(choices, 1):
        print(f"  {index}. {label}")
    try:
        selected = int(input("Opción: ")) - 1
        if 0 <= selected < len(choices):
            return choices[selected][0]
    except ValueError:
        pass
    raise ThinkISPError("Selecciona una de las opciones mostradas.")


def _band_name(value):
    return {"bg": "2.4 GHz", "a": "5 GHz", "6GHz": "6 GHz"}.get(value, value)


def technical_properties():
    """El propio nmcli enumera las propiedades que acepta esta versión del OS."""
    from thinkisp.core.profile import _offline_template
    import re
    template = _offline_template()
    result = {}
    for group in ("802-11-wireless", "802-11-wireless-security", "ipv4", "ipv6", "tc"):
        try:
            run_cmd(["nmcli", "--offline", "connection", "modify", f"{group}.__thinkisp__", ""],
                    input_text=template, check=True)
        except CommandError as exc:
            match = re.search(r"\[([^\]]+)\]", exc.detail)
            if match:
                result[group] = [name.strip() for name in match[1].split(",")]
    return result


def configure_hotspot():
    if not sys.stdin.isatty():
        raise ThinkISPError("La configuración requiere una terminal interactiva: ejecuta 'thinkisp setup'.")
    caps = inspect_capabilities()
    existing = profile_uuid()
    settings = dict(BASE_FIELDS)
    tx_power = None
    if existing:
        kind = profile_values(["connection.type", "802-11-wireless.mode"], existing, check=True)
        if kind["connection.type"] != "802-11-wireless" or kind["802-11-wireless.mode"] != "ap":
            raise ThinkISPError(f"El nombre {HOTSPOT_CONN!r} está ocupado por otra conexión. Elige THINKISP_PROFILE.")
        values = profile_values(list(settings), existing, check=True, secrets=True)
        for key, value in values.items():
            if key.endswith((".mtu", ".powersave")):
                value = re.sub(r"^(-?\d+)\s+\(.*\)$", r"\1", value)
            settings[key] = value
        from thinkisp.core.network import load_state
        tx_power = load_state().get("radio", {}).get(existing, {}).get("tx_power_dbm")
        for key, supported in caps.optional.items():
            if supported:
                value = profile_values([key], existing, check=True)[key]
                settings[key] = re.sub(r"^(-?\d+)\s+\(.*\)$", r"\1", value)
        if caps.optional.get("802-11-wireless.channel-width"):
            value = profile_values(["802-11-wireless.channel-width"], existing, check=True)["802-11-wireless.channel-width"]
            settings["802-11-wireless.channel-width"] = re.sub(r"^(\d+)\s+\(.*\)$", r"\1", value)
            if settings["802-11-wireless.channel-width"] == "0":
                settings["802-11-wireless.channel-width"] = "auto"
    else:
        print(f"\nthinkISP · Primera configuración · {socket.gethostname()}")
        print(f"Adaptador: {caps.interface} · controlador: {caps.driver} · radio: {caps.phy}")
        settings["802-11-wireless.ssid"] = input(f"SSID [{default_ssid()}]: ") or default_ssid()
        settings["802-11-wireless-security.psk"] = _password()

    while True:
        password = "Configurada" if settings.get("802-11-wireless-security.psk") else "Pendiente"
        print(f"\nthinkISP · CONFIGURACIÓN · {socket.gethostname()} · {caps.interface} ({caps.driver})")
        print(f"Perfil: {HOTSPOT_CONN} · bandas AP disponibles: {', '.join(_band_name(b) for b in caps.bands)}")
        print(f"  1 SSID: {settings['802-11-wireless.ssid']}")
        print(f"  2 Contraseña: {password}")
        print(f"  3 Seguridad: {settings['802-11-wireless-security.key-mgmt']}")
        print(f"  4 Banda: {_band_name(settings['802-11-wireless.band']) or 'Automática'}")
        print(f"  5 Canal: {settings['802-11-wireless.channel'] or '0'} (0 = automático)")
        print(f"  6 Ancho de canal: {settings.get('802-11-wireless.channel-width') or 'auto'}")
        print(f"  7 Límite de potencia TX: {str(tx_power) + ' dBm' if tx_power is not None else 'Automática del sistema'}")
        print(f"  8 SSID oculto: {settings['802-11-wireless.hidden']}")
        print(f"  9 Ahorro de energía: {settings['802-11-wireless.powersave']} (0 = sistema)")
        print(f" 10 MTU: {settings['802-11-wireless.mtu'] or '0'} (0 = automático)")
        isolation = (settings.get('802-11-wireless.ap-isolation') or 'Sistema') if caps.optional.get('802-11-wireless.ap-isolation') else 'No expuesto por NetworkManager'
        pmf = (settings.get('802-11-wireless-security.pmf') or 'Sistema') if caps.optional.get('802-11-wireless-security.pmf') else 'No expuesta por NetworkManager'
        print(f" 11 Aislamiento entre clientes: {isolation}")
        print(f" 12 Protección de tramas (PMF): {pmf}")
        print(f" 13 Dirección/subred IPv4: {settings['ipv4.addresses'] or 'Automática de NetworkManager'}")
        print(f" 14 Rango DHCP: {settings.get('ipv4.shared-dhcp-range') or 'Automático / revisar capacidades'}")
        print(f" 15 Tiempo de concesión DHCP: {settings.get('ipv4.shared-dhcp-lease-time') or 'Sistema / revisar capacidades'}")
        print("  A Opciones técnicas de NetworkManager · I Capacidades y restricciones completas")
        print("  S Guardar (si está activo se reiniciará el hotspot) · C Cancelar")
        try:
            choice = input("Opción: ").strip().lower()
            if choice == "c":
                raise KeyboardInterrupt
            if choice == "i":
                print(caps.raw)
                print(caps.regulatory)
                print("Los canales 'disabled' y 'no IR' no están disponibles para iniciar un AP.")
                print("DFS requiere soporte de radar/CAC del controlador; la activación puede tardar o ser rechazada.")
            elif choice == "1":
                settings["802-11-wireless.ssid"] = input("SSID: ")
            elif choice == "2":
                settings["802-11-wireless-security.psk"] = _password()
            elif choice == "3":
                settings["802-11-wireless-security.key-mgmt"] = _pick("Seguridad disponible en el backend:", [
                    (v, f"{v} · {caps.security_notes[v]}" if v in caps.security_notes else v) for v in caps.security])
            elif choice == "4":
                settings["802-11-wireless.band"] = _pick("Banda:", [("", "Automática")] + [(v, _band_name(v)) for v in caps.bands])
                settings["802-11-wireless.channel"] = "0"
                if caps.optional.get('802-11-wireless.channel-width'):
                    settings['802-11-wireless.channel-width'] = 'auto'
            elif choice == "5":
                band = settings["802-11-wireless.band"]
                if not band:
                    raise ThinkISPError("Selecciona primero una banda para fijar un canal.")
                channels = [c for c in caps.channels if c.usable and c.band == band]
                settings["802-11-wireless.channel"] = _pick("Canales disponibles:", [("0", "Automático")] + [
                    (str(c.number), f"{c.number} · {c.frequency} MHz · {c.flags}") for c in channels])
            elif choice == "6":
                band = settings['802-11-wireless.band']
                widths = caps.widths_by_band.get(band, caps.widths) if caps.widths_by_band else caps.widths
                settings["802-11-wireless.channel-width"] = _pick("Anchos anunciados y aceptados por NetworkManager:", [(v, v) for v in widths])
                if not caps.optional.get("802-11-wireless.channel-width") and len(caps.widths) == 1:
                    settings.pop("802-11-wireless.channel-width")
            elif choice == "7":
                print("El controlador limita la potencia según el país y el canal. Aplicar un límite requiere permisos para iw.")
                value = input("Potencia en dBm o 'auto': ").strip().lower()
                tx_power = None if value == "auto" else float(value)
            elif choice == "8":
                settings["802-11-wireless.hidden"] = _pick("Visibilidad:", [("no", "Visible"), ("yes", "Oculto")])
            elif choice == "9":
                settings["802-11-wireless.powersave"] = _pick("Ahorro de energía:", [("0", "Sistema"), ("1", "No modificar"), ("2", "Desactivado"), ("3", "Activado")])
            elif choice == "10":
                settings["802-11-wireless.mtu"] = input("MTU (0 = automático): ").strip()
            elif choice in ("11", "12", "14", "15"):
                key = {"11": "802-11-wireless.ap-isolation", "12": "802-11-wireless-security.pmf",
                       "14": "ipv4.shared-dhcp-range", "15": "ipv4.shared-dhcp-lease-time"}[choice]
                if not caps.optional.get(key):
                    raise ThinkISPError("Esta versión de NetworkManager no expone esa opción.")
                settings[key] = input(f"{key} (vacío = sistema): ").strip()
            elif choice == "13":
                settings["ipv4.addresses"] = input("IPv4/prefijo (vacío = automático): ").strip()
            elif choice == "a":
                supported = technical_properties()
                for group, names in supported.items():
                    print(f"{group}: {', '.join(names)}")
                prop = input("Propiedad completa (vacío = volver): ").strip()
                if prop:
                    group, _, name = prop.rpartition(".")
                    if name not in supported.get(group, []):
                        raise ThinkISPError("La propiedad no está expuesta por esta versión de NetworkManager.")
                    value = getpass.getpass("Valor: ") if any(v in name for v in ("psk", "password", "wep-key")) else input("Valor: ")
                    settings[prop] = value
            elif choice == "s":
                validate_settings(settings, caps, tx_power)
                active = is_hotspot_active(check=True)
                profile = save_profile(settings, caps, tx_power)
                if active:
                    run_cmd(["nmcli", "--wait", "60", "connection", "up", "uuid", profile], check=True, timeout=65)
                    apply_radio_settings(profile)
                print("Configuración guardada en NetworkManager.")
                return profile
            else:
                raise ThinkISPError("Selecciona una opción del panel.")
        except (ThinkISPError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)


def ensure_setup():
    if profile_uuid() is None:
        configure_hotspot()
