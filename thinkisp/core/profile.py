"""Perfiles AP y capacidades obtenidas de NetworkManager y del controlador Linux."""

from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import uuid

from thinkisp.config import HOTSPOT_CONN, WLAN_IFACE, default_ssid
from thinkisp.core.network import ThinkISPError, load_state, run_cmd, save_state, split_nmcli, state_lock


def profile_uuid():
    """No confundir un perfil ausente con un fallo de NetworkManager."""
    out = run_cmd(["nmcli", "-t", "-f", "NAME,UUID", "connection", "show"],
                  check=True, strip=False)
    matches = [parts[1] for line in out.splitlines()
               for parts in [split_nmcli(line)]
               if len(parts) == 2 and parts[0] == HOTSPOT_CONN]
    if len(matches) > 1:
        raise ThinkISPError(f"Hay varios perfiles llamados {HOTSPOT_CONN!r}. Selecciona uno con THINKISP_PROFILE.")
    return matches[0] if matches else None


def profile_values(properties, profile=None, check=False, secrets=False):
    args = ["nmcli", "-e", "no"]
    if secrets:
        args.append("--show-secrets")
    args += ["-g", ",".join(properties), "connection", "show"]
    args += ["uuid", profile] if profile else ["id", HOTSPOT_CONN]
    out = run_cmd(args, check=check, strip=False)
    values = out.split("\n") if out is not None else []
    return dict(zip(properties, values + [""] * len(properties)))


def require_hotspot_profile():
    profile = profile_uuid()
    if profile is None:
        raise ThinkISPError("Primera configuración pendiente. Ejecuta 'thinkisp setup' en una terminal.")
    fields = ["connection.type", "802-11-wireless.mode", "ipv4.method", "connection.interface-name"]
    values = profile_values(fields, profile, check=True)
    if (values[fields[0]] != "802-11-wireless" or values[fields[1]] != "ap"
            or values[fields[2]] != "shared"):
        raise ThinkISPError(f"El perfil {HOTSPOT_CONN!r} no es un AP con conexión compartida. Revisa 'thinkisp setup'.")
    if values[fields[3]] and values[fields[3]] != WLAN_IFACE:
        raise ThinkISPError(f"El perfil usa {values[fields[3]]}; el adaptador seleccionado es {WLAN_IFACE or 'ninguno'}. Revisa 'thinkisp setup'.")
    return profile


@dataclass
class Channel:
    number: int
    frequency: int
    max_dbm: float = None
    flags: str = ""

    @property
    def band(self):
        if 2400 <= self.frequency < 2500:
            return "bg"
        if 4900 <= self.frequency < 5925:
            return "a"
        if 5925 <= self.frequency <= 7125:
            return "6GHz"
        return None  # No atribuir otra banda a una radio que el backend no gestiona.

    @property
    def usable(self):
        return not any(flag in self.flags.lower() for flag in ("disabled", "no ir", "no-ir", "passive scanning"))


@dataclass
class Capabilities:
    interface: str
    phy: str
    driver: str
    channels: list = field(default_factory=list)
    widths: list = field(default_factory=lambda: ["auto"])
    security: list = field(default_factory=lambda: ["wpa-psk"])
    security_notes: dict = field(default_factory=dict)
    optional: dict = field(default_factory=dict)
    regulatory: str = ""
    raw: str = ""
    nm_bands: set = None
    widths_by_band: dict = None

    @property
    def bands(self):
        bands = {channel.band for channel in self.channels if channel.usable and channel.band}
        return sorted(bands if self.nm_bands is None else bands & self.nm_bands)


def parse_channels(text):
    channels = []
    for line in text.splitlines():
        match = re.search(r"\*\s+(\d+(?:\.\d+)?)\s+MHz\s+\[(\d+)\](.*)", line)
        if match:
            frequency, number, flags = match.groups()
            power = re.search(r"(-?[\d.]+) dBm", flags)
            frequency = float(frequency)
            frequency = int(frequency) if frequency.is_integer() else frequency
            channels.append(Channel(int(number), frequency, float(power[1]) if power else None, flags.strip()))
    return channels


def channel_allows_width(channel, width):
    flags = re.sub(r'\s+', '', channel.flags.lower())
    if f'no{width}mhz' in flags:
        return False
    if str(width) == '40' and ('noht40+' in flags and 'noht40-' in flags):
        return False
    return True


def radio_widths_by_band(raw):
    """No trasladar capacidades de una banda a otra ni leer restricciones como soporte."""
    blocks = re.split(r'^\s*Band \d+:\s*$', raw, flags=re.M)
    result = {}
    for block in blocks:
        bands = {channel.band for channel in parse_channels(block) if channel.band}
        lines = [line for line in block.splitlines() if not re.search(r'\*\s+\d+(?:\.\d+)?\s+MHz', line)]
        text = '\n'.join(line for line in lines if 'neither' not in line.lower())
        widths = {'20'}
        if re.search(r'HT20/HT40|HE40|EHT40', text):
            widths.add('40')
        if re.search(r'VHT Capabilities|HE80|EHT80|\b80\s*MHz\b', text):
            widths.add('80')
        for width in (160, 320):
            if re.search(rf'(?:HE|EHT){width}|\b{width}\s*MHz\b', text, re.I):
                widths.add(str(width))
        for band in bands:
            allowed = {w for w in widths if (int(w) <= 40 or band != 'bg') and (int(w) < 320 or band == '6GHz')}
            result.setdefault(band, set()).update(allowed)
    return result


def _offline_template():
    return run_cmd(["nmcli", "--offline", "connection", "add", "type", "wifi",
                    "con-name", HOTSPOT_CONN, "ssid", default_ssid(),
                    "802-11-wireless.mode", "ap", "ipv4.method", "shared",
                    "802-11-wireless-security.key-mgmt", "wpa-psk"], check=True)


def probe_property(template, name, value):
    # Offline: no se crea ni se cambia ninguna conexión real.
    return run_cmd(["nmcli", "--offline", "connection", "modify", name, value],
                   input_text=template) is not None


def inspect_capabilities(allow_unusable=False):
    if not WLAN_IFACE:
        raise ThinkISPError("No se detectó un adaptador Wi-Fi. Conecta uno y vuelve a ejecutar thinkISP.")
    ap = run_cmd(["nmcli", "-g", "WIFI-PROPERTIES.AP", "device", "show", WLAN_IFACE], check=True)
    if ap != "yes":
        raise ThinkISPError(f"{WLAN_IFACE} no anuncia soporte AP en NetworkManager.")
    info = run_cmd(["iw", "dev", WLAN_IFACE, "info"], check=True)
    wiphy = re.search(r"\bwiphy\s+(\d+)", info)
    if not wiphy:
        raise ThinkISPError(f"No se pudo identificar la radio de {WLAN_IFACE}.")
    phy = f"phy{wiphy[1]}"
    raw = run_cmd(["iw", "phy", phy, "info"], check=True)
    driver_path = Path(f"/sys/class/net/{WLAN_IFACE}/device/driver")
    driver = driver_path.resolve().name if driver_path.exists() else "No informado"
    caps = Capabilities(WLAN_IFACE, phy, driver, parse_channels(raw), raw=raw)
    caps.regulatory = run_cmd(["iw", "reg", "get"], check=True)
    template = _offline_template()
    caps.nm_bands = {band for band in caps.bands
                     if probe_property(template, "802-11-wireless.band", band)}
    # Una anchura se ofrece sólo si la banda de la radio Y nmcli la aceptan.
    advertised = radio_widths_by_band(raw)
    caps.widths_by_band = {band: ['auto'] for band in caps.bands}
    for band in caps.bands:
        for width in sorted(advertised.get(band, {'20'}), key=int):
            candidates = [c for c in caps.channels if c.usable and c.band == band and channel_allows_width(c, width)]
            if not candidates:
                continue
            channel = candidates[0]
            width_template = run_cmd(["nmcli", "--offline", "connection", "modify",
                                      "802-11-wireless.band", band,
                                      "802-11-wireless.channel", str(channel.number)],
                                     input_text=template, check=True)
            if probe_property(width_template, "802-11-wireless.channel-width", width):
                caps.widths_by_band[band].append(width)
    caps.widths = sorted({width for widths in caps.widths_by_band.values() for width in widths},
                        key=lambda width: 0 if width == 'auto' else int(width))
    wpa3 = run_cmd(["nmcli", "-g", "WIFI-PROPERTIES.WPA3", "device", "show", WLAN_IFACE])
    if wpa3 != "no" and probe_property(template, "802-11-wireless-security.key-mgmt", "sae"):
        caps.security.append("sae")
        if wpa3 != "yes" and "SAE" not in raw:
            caps.security_notes["sae"] = "NetworkManager lo acepta; soporte de radio sin confirmar"
    for name, value in {
        "802-11-wireless.ap-isolation": "1",
        "802-11-wireless-security.pmf": "2",
        "ipv4.shared-dhcp-range": "",
        "ipv4.shared-dhcp-lease-time": "0",
        "802-11-wireless.channel-width": "auto",
    }.items():
        caps.optional[name] = probe_property(template, name, value)
    if not caps.bands and not allow_unusable:
        if not caps.channels:
            raise ThinkISPError(f'No se pudo interpretar la lista de canales de iw para {WLAN_IFACE}.')
        if any(c.usable and c.band for c in caps.channels):
            raise ThinkISPError('nmcli no aceptó las bandas disponibles. Revisa la versión de NetworkManager.')
        raise ThinkISPError(f"{WLAN_IFACE} no anuncia canales autorizados para AP. Revisa el dominio regulatorio del sistema.")
    return caps


def validate_settings(settings, caps, tx_power=None):
    ssid = settings.get("802-11-wireless.ssid", "")
    if not 1 <= len(ssid.encode("utf-8")) <= 32 or any(c in ssid for c in "\n\r\0"):
        raise ThinkISPError("El SSID debe ocupar entre 1 y 32 bytes y no contener saltos de línea.")
    security = settings.get("802-11-wireless-security.key-mgmt", "wpa-psk")
    if security not in caps.security:
        raise ThinkISPError("La seguridad elegida no está anunciada por este equipo.")
    password = settings.get("802-11-wireless-security.psk", "")
    valid_hex = bool(re.fullmatch(r"[0-9a-fA-F]{64}", password))
    if security == "wpa-psk" and not (valid_hex or (8 <= len(password) <= 63 and all(32 <= ord(char) <= 126 for char in password))):
        raise ThinkISPError("La contraseña WPA debe tener 8–63 caracteres ASCII o 64 dígitos hexadecimales.")
    if security == "sae" and (not password or any(c in password for c in "\n\r\0")):
        raise ThinkISPError("WPA3 requiere una contraseña sin saltos de línea.")
    # Some nmcli versions accept out-of-range numeric enums without rejecting
    # the serialized keyfile. Validate the controls we expose before any write.
    enum_fields = {
        '802-11-wireless.powersave': ('ahorro de energía', {'', '0', '1', '2', '3', 'default', 'ignore', 'disable', 'enable'}),
        '802-11-wireless-security.pmf': ('protección de tramas PMF', {'', '0', '1', '2', '3', 'default', 'disable', 'optional', 'required'}),
        '802-11-wireless.ap-isolation': ('aislamiento entre clientes', {'', '-1', '0', '1', 'default', 'no', 'yes', 'false', 'true'}),
        '802-11-wireless.hidden': ('visibilidad', {'', '0', '1', 'no', 'yes', 'false', 'true'}),
    }
    for key, (label, allowed) in enum_fields.items():
        if key in settings and str(settings[key]).lower() not in allowed:
            raise ThinkISPError(f'El valor de {label} no es válido. Elige una opción del formulario.')
    band = settings.get("802-11-wireless.band", "")
    if band and band not in caps.bands:
        raise ThinkISPError("La banda elegida no tiene canales AP disponibles en este equipo.")
    if band == '6GHz':
        if security != 'sae':
            raise ThinkISPError('La banda de 6 GHz requiere WPA3 (sae); no se admite WPA2 en esa banda.')
        if settings.get('802-11-wireless-security.pmf', '') not in ('', '0', 'default', '3', 'required'):
            raise ThinkISPError('En 6 GHz, PMF debe ser obligatorio o quedar a cargo de WPA3.')
    try:
        channel = int(settings.get("802-11-wireless.channel") or "0")
    except ValueError as exc:
        raise ThinkISPError("El canal debe ser un número o 0 para selección automática.") from exc
    available = [c for c in caps.channels if c.usable and (not band or c.band == band)]
    if channel:
        available = [c for c in available if c.number == channel]
        if not available or not band:
            raise ThinkISPError("Elige una banda y un canal AP disponible para esa banda.")
    width = settings.get("802-11-wireless.channel-width", "auto")
    if width in ("", "0"):
        width = "auto"
    if width not in caps.widths:
        raise ThinkISPError("El ancho de canal no está disponible en esta tarjeta y versión de NetworkManager.")
    if band and caps.widths_by_band is not None and width not in caps.widths_by_band.get(band, []):
        raise ThinkISPError('El ancho está disponible en otra banda de la radio, no en la seleccionada.')
    if width != "auto":
        if not channel or not band:
            raise ThinkISPError("Un ancho de canal fijo requiere elegir primero banda y canal.")
        if (band == "bg" and int(width) > 40) or (band != "6GHz" and int(width) >= 320):
            raise ThinkISPError("El ancho elegido no corresponde a esa banda.")
        if any(not channel_allows_width(c, width) for c in available):
            raise ThinkISPError("El canal anuncia una restricción para ese ancho.")
    if tx_power is not None:
        limits = [c.max_dbm for c in available if c.max_dbm is not None]
        if not limits or not math.isfinite(tx_power) or not -(2 ** 31) <= tx_power * 100 <= min(limits) * 100:
            raise ThinkISPError("La potencia solicitada excede el límite anunciado para los canales elegidos.")
    if settings.get("802-11-wireless.mode", "ap") != "ap" or settings.get("ipv4.method", "shared") != "shared":
        raise ThinkISPError("El perfil de thinkISP debe mantener modo AP e IPv4 compartido.")


def settings_arguments(settings):
    """Translate form defaults to nmcli reset syntax; 0 is not a channel setter."""
    normalized = dict(settings)
    channel = '802-11-wireless.channel'
    width = '802-11-wireless.channel-width'
    if channel in normalized and str(normalized[channel]) in ('', '0'):
        normalized[channel] = ''
    if width in normalized and str(normalized[width]) in ('', '0'):
        normalized[width] = 'auto'
    # Apply dependent radio properties in a stable order regardless of edit order.
    radio = ('802-11-wireless.band', channel, width)
    ordered = [(key, value) for key, value in normalized.items() if key not in radio]
    ordered += [(key, normalized[key]) for key in radio if key in normalized]
    return [part for key, value in ordered for part in (key, str(value))]


def save_profile(settings, caps, tx_power=None):
    """Valida antes de escribir y nunca sobrescribe un perfil ajeno por accidente."""
    validate_settings(settings, caps, tx_power)
    settings = dict(settings)
    settings.update({"802-11-wireless.mode": "ap", "ipv4.method": "shared"})
    # Validar todo el conjunto con el parser del NetworkManager instalado.
    pairs = settings_arguments(settings)
    run_cmd(["nmcli", "--offline", "connection", "modify", *pairs],
            input_text=_offline_template(), check=True)
    existing = profile_uuid()
    if existing:
        kind = profile_values(["connection.type", "802-11-wireless.mode"], existing, check=True)
        if kind["connection.type"] != "802-11-wireless" or kind["802-11-wireless.mode"] != "ap":
            raise ThinkISPError(f"{HOTSPOT_CONN!r} pertenece a otro tipo de conexión. Usa otro THINKISP_PROFILE.")
        run_cmd(["nmcli", "connection", "modify", "uuid", existing,
                 "connection.interface-name", caps.interface, *pairs], check=True)
        profile = existing
    else:
        profile = str(uuid.uuid4())
        run_cmd(["nmcli", "connection", "add", "type", "wifi", "ifname", caps.interface,
                 "con-name", HOTSPOT_CONN, "connection.uuid", profile,
                 "connection.autoconnect", "no", *pairs], check=True)
    with state_lock() as state:
        # Sin contraseña en JSON: NetworkManager es el almacén de credenciales.
        previous = state.setdefault("radio", {}).get(profile, {})
        state["radio"][profile] = {"tx_power_dbm": tx_power,
                                   "manage_tx_power": tx_power is not None or previous.get("manage_tx_power", False)}
        save_state(state)
    return profile


def apply_radio_settings(profile):
    config = load_state().get("radio", {}).get(profile, {})
    power = config.get("tx_power_dbm")
    args = ["iw", "dev", WLAN_IFACE, "set", "txpower"]
    if power is None:
        args.append("auto")
    else:
        args += ["limit", str(round(float(power) * 100))]  # iw utiliza mBm.
    # Sólo se toca potencia si el usuario guardó ajustes para este perfil.
    if config.get("manage_tx_power", power is not None):
        run_cmd(args, check=True)


def hotspot_details(secrets=False):
    fields = ["802-11-wireless.ssid", "802-11-wireless.band", "802-11-wireless.channel",
              "802-11-wireless.hidden", "802-11-wireless-security.key-mgmt", "connection.zone"]
    values = profile_values(fields)
    data = {"ssid": values[fields[0]] or "Sin configurar", "radio": "Sin datos",
            "hidden": values[fields[3]] == "yes", "security": values[fields[4]] or "Sin datos",
            "firewall": values[fields[5]] or "Predeterminada", "interface": WLAN_IFACE or "No detectada"}
    if WLAN_IFACE:
        info = run_cmd(["iw", "dev", WLAN_IFACE, "info"])
        channel = re.search(r"channel (\d+) \((\d+(?:\.\d+)?) MHz\)(?:, width: ([^,\n]+))?", info or "")
        if channel:
            data["radio"] = f"{channel[2]} MHz · canal {channel[1]}"
            if channel[3]:
                data["radio"] += f" · {channel[3]}"
        power = re.search(r"txpower ([\d.]+) dBm", info or "")
        data["tx_power"] = f"{power[1]} dBm" if power else "Sin datos"
    if secrets:
        result = profile_values(["802-11-wireless.ssid", "802-11-wireless-security.psk"],
                                check=True, secrets=True)
        data["ssid"] = result["802-11-wireless.ssid"]
        data["psk"] = result["802-11-wireless-security.psk"]
        if not data["psk"]:
            raise ThinkISPError("NetworkManager no entregó la contraseña del perfil. Revisa sus permisos.")
    return data
