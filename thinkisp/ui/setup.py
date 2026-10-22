"""Panel de configuración reutilizable desde CLI y dashboard."""

import socket
import sys
import re

from thinkisp.ui import theme
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


def load_configuration():
    """Consulta datos sin tocar curses ni modificar la red."""
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
        settings["802-11-wireless.channel"] = settings.get("802-11-wireless.channel") or "0"
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
        settings['802-11-wireless.ssid'] = default_ssid()
    return caps, existing, settings, tx_power


def persist_configuration(settings, caps, tx_power):
    validate_settings(settings, caps, tx_power)
    active = is_hotspot_active(check=True)
    profile = save_profile(settings, caps, tx_power)
    if active:
        run_cmd(['nmcli', '--wait', '60', 'connection', 'up', 'uuid', profile], check=True, timeout=65)
        apply_radio_settings(profile)
    return profile


# label, property, hint. Values and supported options always come from the host.
GROUPS = [
    ('Red', [
        ('Nombre de la red', '802-11-wireless.ssid', 'SSID visible en los dispositivos. Entre 1 y 32 bytes.'),
        ('Contraseña', '802-11-wireless-security.psk', 'Se mantiene oculta. Enter permite sustituirla.'),
        ('Seguridad', '802-11-wireless-security.key-mgmt', 'Métodos anunciados por el backend; la activación confirma el soporte real.'),
        ('Visibilidad', '802-11-wireless.hidden', 'Una red oculta sigue siendo detectable por herramientas Wi-Fi.'),
    ]),
    ('Radio', [
        ('Banda', '802-11-wireless.band', 'Bandas con canales AP anunciados por la tarjeta y el sistema.'),
        ('Canal', '802-11-wireless.channel', 'DFS requiere detección de radar. Se indican restricciones de cada canal.'),
        ('Ancho de canal', '802-11-wireless.channel-width', 'Un ancho fijo requiere banda y canal; el firmware puede rechazarlo.'),
        ('Potencia TX', 'tx_power', 'Límite en dBm. Se respetan los límites del país y del canal.'),
        ('Ahorro de energía', '802-11-wireless.powersave', 'Control del ahorro energético que expone NetworkManager.'),
        ('MTU', '802-11-wireless.mtu', 'Tamaño máximo de paquete. 0 utiliza el valor automático.'),
    ]),
    ('Acceso', [
        ('Aislar clientes', '802-11-wireless.ap-isolation', 'Impide comunicación directa entre clientes cuando el backend lo permite.'),
        ('Protección de tramas', '802-11-wireless-security.pmf', 'PMF protege las tramas de gestión; WPA3 lo requiere.'),
    ]),
    ('Direcciones', [
        ('Subred IPv4', 'ipv4.addresses', 'Dirección/prefijo del router. Vacío utiliza la selección automática.'),
        ('Rango DHCP', 'ipv4.shared-dhcp-range', 'Primera y última dirección del rango. Vacío utiliza el sistema.'),
        ('Concesión DHCP', 'ipv4.shared-dhcp-lease-time', 'Duración en segundos; vacío deja el valor del sistema.'),
    ]),
    ('Técnico', [
        ('Propiedades avanzadas', 'advanced', 'Explora las propiedades expuestas por la versión instalada de NetworkManager.'),
        ('Capacidades de la radio', 'capabilities', 'Información y restricciones completas del adaptador.'),
    ]),
]


class SetupForm:
    def __init__(self, ui, configuration):
        import copy
        self.ui = ui
        self.caps, self.existing, self.settings, self.tx_power = configuration
        self.initial = (copy.deepcopy(self.settings), self.tx_power)
        self.tab, self.selected = 0, 0
        self.error = ''

    @property
    def dirty(self):
        return (self.settings, self.tx_power) != self.initial

    def supported(self, key):
        if key == '802-11-wireless.channel-width':
            return bool(self.caps.optional.get(key))
        if key in ('802-11-wireless.ap-isolation', '802-11-wireless-security.pmf',
                   'ipv4.shared-dhcp-range', 'ipv4.shared-dhcp-lease-time'):
            return bool(self.caps.optional.get(key))
        return True

    def display(self, key):
        if not self.supported(key):
            return 'No disponible en este sistema'
        value = self.settings.get(key, 'auto' if key.endswith('.channel-width') else '')
        if key.endswith('.psk'):
            return '••••••••  Configurada' if value else 'Pendiente · introduce una contraseña'
        if key == 'tx_power':
            return f'{self.tx_power:g} dBm' if self.tx_power is not None else 'Automática'
        if key in ('advanced', 'capabilities'):
            return 'Abrir →'
        choices = self.choices(key)
        if choices:
            return next((label for v, label in choices if v == value), value or 'Sistema')
        return value or 'Automático'

    def choices(self, key):
        c = self.caps
        if key.endswith('.key-mgmt'):
            return [(v, {'wpa-psk': 'WPA2 Personal', 'sae': 'WPA3 Personal'}.get(v, v) +
                     (f' · {c.security_notes[v]}' if v in c.security_notes else '')) for v in c.security]
        if key.endswith('.band'):
            return [('', 'Automática')] + [(b, _band_name(b)) for b in c.bands]
        if key.endswith('.channel'):
            band = self.settings.get('802-11-wireless.band')
            return [('0', 'Automático')] + [(str(ch.number), f'{ch.number} · {ch.frequency} MHz · {ch.flags}')
                                            for ch in c.channels if ch.usable and ch.band == band]
        if key.endswith('.channel-width'):
            band = self.settings.get('802-11-wireless.band')
            widths = c.widths_by_band.get(band, c.widths) if c.widths_by_band else c.widths
            return [(v, 'Automático' if v == 'auto' else f'{v} MHz') for v in widths]
        if key.endswith('.hidden'):
            return [('no', 'Visible'), ('yes', 'Oculta')]
        if key.endswith('.powersave'):
            return [('0', 'Sistema'), ('1', 'No modificar'), ('2', 'Desactivado'), ('3', 'Activado')]
        if key.endswith('.ap-isolation'):
            return [('', 'Sistema'), ('0', 'Desactivado'), ('1', 'Activado')]
        if key.endswith('.pmf'):
            return [('0', 'Sistema'), ('1', 'Desactivada'), ('2', 'Opcional'), ('3', 'Obligatoria')]
        return None

    def edit_field(self, label, key, hint):
        if not self.supported(key):
            raise ThinkISPError('Esta versión de NetworkManager no expone esa opción.')
        if key == 'capabilities':
            self.ui.message('Capacidades y regulación', self.caps.raw + '\n' + self.caps.regulatory)
            return
        if key == 'advanced':
            supported = self.ui.work('Consultando propiedades', technical_properties)
            options = [(f'{group}.{name}', f'{group}.{name}') for group, names in supported.items() for name in names]
            prop = self.ui.choose('Propiedades de NetworkManager', options)
            if prop is not None:
                secret = any(v in prop for v in ('psk', 'password', 'wep-key', 'secret'))
                value = self.ui.edit(prop, self.settings.get(prop, ''), secret=secret,
                                     hint='Se validará con NetworkManager antes de guardar.')
                if value is not None:
                    self.settings[prop] = value
            return
        if key.endswith('.channel') and not self.settings.get('802-11-wireless.band'):
            raise ThinkISPError('Elige primero una banda en la fila anterior para seleccionar un canal.')
        if key == 'tx_power':
            value = self.ui.edit(label, self.tx_power if self.tx_power is not None else 'auto', hint=hint)
            if value is not None:
                self.tx_power = None if value.lower().strip() in ('', 'auto') else float(value)
            return
        choices = self.choices(key)
        value = self.ui.choose(label, choices, self.settings.get(key)) if choices else self.ui.edit(
            label, self.settings.get(key, ''), secret=key.endswith('.psk'), hint=hint)
        if value is None:
            return
        if key.endswith('.psk'):
            repeat = self.ui.edit('Confirmar contraseña', secret=True, hint='Escribe nuevamente la contraseña.')
            if repeat is None:
                return
            if repeat != value:
                raise ThinkISPError('Las contraseñas no coinciden. No se cambió la contraseña.')
        self.settings[key] = value
        if key.endswith('.band'):
            self.settings['802-11-wireless.channel'] = '0'
            self.settings.pop('802-11-wireless.channel-width', None)

    def run(self):
        import curses
        from thinkisp.ui.widgets import put, clipped, accent
        win = self.ui.screen
        while True:
            mode = 'Editar red' if self.existing else 'Crear tu red'
            h, w = self.ui.frame(mode + (' · Sin guardar' if self.dirty else ''),
                                f'{socket.gethostname()}  /  {self.caps.interface}  /  {self.caps.driver}',
                                '↑↓ Campo   Tab Sección   Enter Editar   F2 Guardar   Esc Volver')
            if h < 20 or w < 72:
                put(win, 5, 2, 'Amplía la terminal a 72 columnas y 20 filas.')
                if self.ui.key() in ('\x1b', '\x03'):
                    if not self.dirty or self.ui.choose('Hay cambios sin guardar',
                        [('stay', 'Seguir editando'), ('discard', 'Descartar cambios y volver')]) == 'discard':
                        raise KeyboardInterrupt
                continue
            tabs, x = [], 2
            for i, (name, _) in enumerate(GROUPS):
                text = f' {name} '
                put(win, 4, x, text, theme.color(19) | curses.A_BOLD if i == self.tab else theme.color(16))
                tabs.append((x, x + len(text), i))
                x += len(text) + 1
            fields = GROUPS[self.tab][1]
            size = max(1, h - 12)
            offset = max(0, self.selected - size + 1)
            label_width = min(27, w // 3)
            for row, (label, key, _) in enumerate(fields[offset:offset + size], 6):
                index = offset + row - 6
                style = theme.selected() if index == self.selected else theme.color(21 if index % 2 else 7)
                if not self.supported(key):
                    style |= curses.A_DIM
                text = f' {"›" if index == self.selected else " "} {label:<{label_width}} {self.display(key)}'
                put(win, row, 2, clipped(text, w - 5).ljust(w - 5), style)
            put(win, h - 5, 3, fields[self.selected][2], curses.A_DIM)
            put(win, h - 4, 3, self.error or 'Guardar no enciende una red apagada. Si está activa, se reiniciará.', accent())
            key = self.ui.key()
            try:
                if key in ('\x1b', '\x03'):
                    if not self.dirty or self.ui.choose('Hay cambios sin guardar',
                        [('stay', 'Seguir editando'), ('discard', 'Descartar cambios y volver')]) == 'discard':
                        raise KeyboardInterrupt
                elif key in (curses.KEY_F2, '\x13'):
                    validate_settings(self.settings, self.caps, self.tx_power)
                    approved = self.ui.choose('Guardar configuración',
                        [('save', 'Guardar · se reinicia la red si está activa'), ('stay', 'Seguir editando')])
                    if approved == 'save':
                        return self.ui.work('Guardando configuración', lambda: persist_configuration(self.settings, self.caps, self.tx_power))
                elif key in (curses.KEY_LEFT, curses.KEY_BTAB, curses.KEY_RIGHT, '\t'):
                    self.tab = (self.tab + (-1 if key in (curses.KEY_LEFT, curses.KEY_BTAB) else 1)) % len(GROUPS)
                    self.selected, self.error = 0, ''
                elif key == curses.KEY_UP:
                    self.selected = (self.selected - 1) % len(fields)
                elif key == curses.KEY_DOWN:
                    self.selected = (self.selected + 1) % len(fields)
                elif key in ('\n', '\r', curses.KEY_ENTER):
                    self.error = ''
                    self.edit_field(*fields[self.selected])
                elif key == curses.KEY_MOUSE:
                    mx, my, _, flags = curses.getmouse()[1:]
                    if my == 4:
                        for left, right, tab in tabs:
                            if left <= mx < right:
                                self.tab, self.selected = tab, 0
                    elif 6 <= my < 6 + min(size, len(fields)):
                        self.selected = offset + my - 6
                        if flags & curses.BUTTON1_DOUBLE_CLICKED:
                            self.edit_field(*fields[self.selected])
            except (ThinkISPError, ValueError, OSError) as exc:
                self.error = str(exc)


def configure_hotspot(screen=None):
    if not sys.stdin.isatty():
        raise ThinkISPError("La configuración requiere una terminal interactiva: ejecuta 'thinkisp setup'.")
    import curses
    from thinkisp.ui.widgets import TerminalUI
    def configure(win):
        ui = TerminalUI(win)
        configuration = ui.work('Detectando adaptador y configuración', load_configuration)
        return SetupForm(ui, configuration).run()
    if screen is not None:
        return configure(screen)
    try:
        return curses.wrapper(configure)
    except curses.error as exc:
        raise ThinkISPError(f'No se pudo abrir la configuración en esta terminal: {exc}') from exc


def ensure_setup(screen=None):
    if screen is None:
        if profile_uuid() is None:
            configure_hotspot()
    else:
        from thinkisp.ui.widgets import TerminalUI
        if TerminalUI(screen).work('Consultando perfil de red', profile_uuid) is None:
            configure_hotspot(screen=screen)
