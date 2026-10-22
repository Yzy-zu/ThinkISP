"""
thinkISP - Panel de terminal con curses.
La UI procesa entrada y dibuja; los trabajadores consultan y modifican la red.
"""

import curses
import locale
import socket
import os
import sys
import shutil
import copy
import unicodedata
from collections import deque
from types import SimpleNamespace
import time

# Configuración y Core de ThinkISP
from thinkisp.config import ap_interface
from thinkisp.config import (
    ETH_IFACE,
    WLAN_IFACE,
)
from thinkisp.core.dns import (
    benchmark_dns,
    run_speedtest,
    set_adblock,
    set_dns_provider,
)
from thinkisp.core.network import (
    ThinkISPError,
    format_bytes,
    format_duration,
    get_ethernet_status,
    get_hotspot_ip,
    get_iface_bytes,
    get_ip_forward_status,
    is_hotspot_active,
    load_state,
    parse_duration_str,
    run_cmd,
)
from thinkisp.core.qos import (
    apply_boost_mode,
    clear_rate_limit,
    get_gaming_mode,
    get_rate_limit,
    set_gaming_mode,
    set_rate_limit,
)
from thinkisp.core.watcher import (
    cancel_timer,
    get_timer_status,
    set_timer_countdown,
    set_timer_idle,
    start_daemon,
    stop_daemon,
)
from thinkisp.core.wifi import (
    wifi_qr_payload,
    get_blocked_clients,
    get_connected_clients,
    kick_client,
    set_stealth_mode,
    start_hotspot,
    stop_hotspot,
    unkick_client,
)

from thinkisp.core.profile import hotspot_details
from thinkisp.ui.setup import configure_hotspot, ensure_setup
from thinkisp.ui.runtime import PanelTasks
from thinkisp.ui import theme
from thinkisp.core.dns_state import dns_status

# Configurar locale para caracteres Unicode
try:
    locale.setlocale(locale.LC_ALL, "")
except Exception:
    pass

# =====================================================================
# CONSTANTES DE ENFOQUE Y MODALES
# =====================================================================
FOCUS_BUTTONS = "buttons"
FOCUS_CLIENTS = "clients"

MODAL_NONE = None
MODAL_QR = "qr"
MODAL_TIMER = "timer"
MODAL_SPEEDTEST = "speedtest"
MODAL_DNS = "dns"
MODAL_LIMIT = "limit"
MODAL_KICK = "kick"
MODAL_HELP = "help"
MODAL_BLOCKED = "blocked"
MODAL_COMMANDS = "commands"

# Definición de Botones del Dashboard
BUTTON_DEFS = [
    {"id": "toggle", "label": "Punto de acceso", "hotkey": "O", "hint": "Encender o apagar la red compartida"},
    {"id": "config", "label": "Configurar", "hotkey": "C", "hint": "SSID, seguridad y parámetros compatibles con este equipo"},
    {"id": "qr", "label": "Conectar QR", "hotkey": "R", "hint": "Ver credenciales y código QR de conexión"},
    {"id": "timer", "label": "Temporizador", "hotkey": "T", "hint": "Programar apagado por tiempo o inactividad"},
    {"id": "gaming", "label": "Colas CAKE", "hotkey": "G", "hint": "Gestionar las colas para reducir congestión"},
    {"id": "limit", "label": "Límite", "hotkey": "L", "hint": "Limitar el ancho de banda compartido"},
    {"id": "dns", "label": "DNS", "hotkey": "D", "hint": "Comparar proveedores DNS o restaurar el anterior"},
    {"id": "adblock", "label": "Filtro DNS", "hotkey": "A", "hint": "Activar o desactivar AdGuard DNS en la salida"},
    {"id": "stealth", "label": "Ocultar red", "hotkey": "S", "hint": "Ocultar o mostrar el nombre de la red"},
    {"id": "speed", "label": "Medir red", "hotkey": "P", "hint": "Medir conexión HTTP, descarga y subida; consume unos 30 MB"},
    {"id": "boost", "label": "Optimizar", "hotkey": "B", "hint": "Configurar CAKE y desactivar ahorro de energía Wi-Fi"},
    {"id": "help", "label": "Ayuda", "hotkey": "?", "hint": "Consultar controles y navegación"},
    {"id": "quit", "label": "Salir", "hotkey": "Q", "hint": "Cerrar el panel; el punto de acceso sigue en su estado actual"},
]

PAGES = [
    ('Resumen', ('toggle', 'config', 'qr')),
    ('Dispositivos', ('qr', 'access', 'blocked')),
    ('Red', ('config', 'toggle', 'stealth', 'timer', 'qr', 'network_plan')),
    ('Rendimiento', ('gaming', 'limit', 'speed', 'boost')),
    ('DNS', ('dns', 'adblock', 'restore_dns')),
]
BUTTON_DEFS += [
    {'id': 'network_plan', 'label': 'Conectividad', 'hotkey': 'N', 'hint': 'Plan por perfil: AP, entrada de Internet, repetidor y motor del portal (fase 1)'},
    {'id': 'access', 'label': 'Ver dispositivo', 'hotkey': 'K', 'hint': 'Abrir detalles y controlar el acceso del dispositivo seleccionado'},
    {'id': 'blocked', 'label': 'Bloqueados', 'hotkey': 'U', 'hint': 'Ver y permitir acceso a dispositivos bloqueados, aunque estén desconectados'},
    {'id': 'restore_dns', 'label': 'Restaurar DNS', 'hotkey': 'E', 'hint': 'Recuperar la configuración DNS anterior de la salida'},
]


def page_actions(state):
    by_id = {item['id']: item for item in BUTTON_DEFS}
    return [by_id[name] for name in PAGES[state.page][1]]


def select_page(state, index):
    state.page = index % len(PAGES)
    state.btn_index = 0
    state.focus = FOCUS_CLIENTS if state.page == 1 else FOCUS_BUTTONS


# El encabezado se construye con los datos del equipo en render_header().


# =====================================================================
# CLASE DE ESTADO GLOBAL DE LA INTERFAZ (TUIState)
# =====================================================================
class TUIState:
    def __init__(self):
        self.running = True
        self.screen = None
        self.page = 0
        self.traffic_history = deque(maxlen=90)
        self.focus = FOCUS_BUTTONS
        self.btn_index = 0
        self.client_index = 0
        self.client_page_size = 1
        self.modal = MODAL_NONE
        self.modal_data = {}

        # Estado del sistema y hardware
        self.hotspot_active = None
        self.ssid = "Consultando…"
        self.psk = ""
        self.band = "Sin datos"
        self.security = "Sin datos"
        self.interface = ap_interface(WLAN_IFACE) or "No detectada"
        self.hostname = socket.gethostname()
        self.ip_gateway = "Sin datos"
        self.ethernet = {"connected": False, "speed": "Desconocida", "interface": ETH_IFACE}
        self.ip_forward = False

        # Modos y características
        self.gaming_mode = False
        self.adblock_mode = False
        self.stealth_mode = False
        self.current_dns = "Consultando DNS…"
        self.rate_limit = None
        self.timer_info = None

        # Clientes conectados
        self.clients = []

        # Métricas de telemetría y tráfico
        self.session_duration = 0.0
        self.session_downloaded = 0
        self.session_uploaded = 0
        self.instant_down_bps = 0.0
        self.instant_up_bps = 0.0

        # Control interno de deltas para cálculo de velocidad instantánea
        self._last_poll_time = time.monotonic()
        self._last_rx_bytes = None
        self._last_tx_bytes = None

        # Barra de notificaciones de estado inferior
        self.tasks = None
        self.busy_label = ""
        self.busy_mutation = False
        self.exit_requested = False
        self.last_refresh = None
        self.poll_error = ""
        self.status_msg = "Consultando el estado de la red…"
        self.status_msg_time = time.time()
        self.status_msg_color = theme.color(4) if curses.has_colors() else 0

        # Regiones clicables del ratón (y, x, w, h, tipo, id)
        self.mouse_regions = []
        self.modal_regions = []
        self.viewport_width = 80

    def set_status(self, msg, color_pair_id=4):
        """Establece un mensaje de estado temporal en el footer."""
        self.status_msg = msg
        self.status_msg_time = time.time()
        self.status_msg_color = theme.color(color_pair_id) if curses.has_colors() else 0


# =====================================================================
# UTILIDADES DE PINTADO CURSES SEGURO
# =====================================================================
def focus_style():
    return theme.selected()


def cell_width(text):
    return sum(0 if unicodedata.combining(c) else (2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1) for c in text)


def clean_text(text):
    # Los nombres de equipos y redes son datos externos, no instrucciones de terminal.
    return "".join(c if not unicodedata.category(c).startswith('C') else ' ' for c in str(text))


def fit_text(text, width, pad=False):
    text = clean_text(text)
    width = max(0, width)
    if cell_width(text) > width:
        result, used = [], 0
        for char in text:
            size = cell_width(char)
            if used + size > max(0, width - 1):
                break
            result.append(char)
            used += size
        text = ''.join(result) + ('…' if width else '')
    return text + ' ' * max(0, width - cell_width(text)) if pad else text


def safe_addstr(win, y, x, text, attr=0):
    """Recorta por celdas de terminal, incluidos SSID Unicode y controles externos."""
    try:
        max_y, max_x = win.getmaxyx()
        if y < 0 or y >= max_y or x < 0 or x >= max_x:
            return
        if not attr & curses.A_COLOR and hasattr(win, 'inch'):
            attr |= win.inch(y, x) & curses.A_COLOR
        available = max_x - x - (1 if y == max_y - 1 else 0)
        if available > 0:
            win.addstr(y, x, fit_text(text, available), attr)
    except (curses.error, UnicodeError):
        pass


def draw_box(win, y, x, h, w, title="", attr=0, title_attr=0):
    if h < 2 or w < 2:
        return
    for row in range(y, y + h):
        safe_addstr(win, row, x, ' ' * w, theme.color(13))
    edge = theme.color(17)
    safe_addstr(win, y, x, '╭' + '─' * (w - 2) + '╮', edge)
    for row in range(y + 1, y + h - 1):
        safe_addstr(win, row, x, '│', edge)
        safe_addstr(win, row, x + w - 1, '│', edge)
    safe_addstr(win, y + h - 1, x, '╰' + '─' * (w - 2) + '╯', edge)
    if title and cell_width(title) < w - 6:
        safe_addstr(win, y, x + 2, f' {title} ', title_attr or theme.color(5) | curses.A_BOLD)


def init_colors():
    theme.initialize()


# =====================================================================
# ACTUALIZACIÓN DE TELEMETRÍA Y ESTADO
# =====================================================================
def update_telemetry(state):
    """Consulta el estado del sistema, NetworkManager y calcula el flujo de tráfico."""
    now = time.time()
    sampled_at = time.monotonic()
    dt = max(0.1, sampled_at - state._last_poll_time)

    # 1. Estado del Hotspot
    state.hotspot_active = is_hotspot_active(check=True)
    details = hotspot_details()
    state.ssid = details["ssid"]
    state.band = details["radio"]
    state.security = details["security"]
    state.interface = details["interface"]
    state.ethernet = get_ethernet_status()
    state.ip_gateway = get_hotspot_ip()
    state.ip_forward = get_ip_forward_status()

    # 2. Modos y configuraciones
    state.gaming_mode = get_gaming_mode()
    state.current_dns, state.adblock_mode = dns_status()
    state.stealth_mode = details["hidden"]
    state.rate_limit = get_rate_limit()
    state.timer_info = get_timer_status()

    # 3. Métricas de tráfico de red
    rx_bytes, tx_bytes = get_iface_bytes(ap_interface(WLAN_IFACE))

    if state._last_rx_bytes is not None and state._last_tx_bytes is not None and state.hotspot_active:
        # En un punto de acceso Wi-Fi:
        # TX de la tarjeta Wi-Fi = Tráfico entregado a clientes (Descarga de los clientes)
        # RX de la tarjeta Wi-Fi = Tráfico enviado por los clientes (Subida de los clientes)
        delta_tx = max(0, tx_bytes - state._last_tx_bytes)
        delta_rx = max(0, rx_bytes - state._last_rx_bytes)
        state.instant_down_bps = delta_tx / dt
        state.instant_up_bps = delta_rx / dt
    else:
        state.instant_down_bps = 0.0
        state.instant_up_bps = 0.0

    state._last_poll_time = sampled_at
    state._last_rx_bytes = rx_bytes
    state._last_tx_bytes = tx_bytes

    # 4. Estadísticas acumuladas de la sesión activa
    st = load_state()
    session = st.get("session")
    if session and state.hotspot_active:
        state.session_duration = max(0.0, now - session.get("start_time", now))
        state.session_downloaded = max(0, tx_bytes - session.get("start_tx", tx_bytes))
        state.session_uploaded = max(0, rx_bytes - session.get("start_rx", rx_bytes))
    else:
        last_s = st.get("last_session")
        if last_s and not state.hotspot_active:
            state.session_duration = last_s.get("duration", 0)
            state.session_downloaded = last_s.get("downloaded", 0)
            state.session_uploaded = last_s.get("uploaded", 0)
        else:
            state.session_duration = 0.0
            state.session_downloaded = 0
            state.session_uploaded = 0

    # Compare station counters by MAC; unknown/reset counters remain unmeasured.
    previous_clients = {item['mac']: item for item in state.clients}
    if state.hotspot_active:
        state.clients = get_connected_clients(check=True)
        blocked = set(get_blocked_clients())
        for client in state.clients:
            client['blocked'] = client.get('mac') in blocked
            previous = previous_clients.get(client['mac'], {})
            for counter, rate in [('tx_bytes', 'down_bps'), ('rx_bytes', 'up_bps')]:
                value, baseline = client.get(counter), previous.get(counter)
                client[rate] = (value - baseline) / dt if value is not None and baseline is not None and value >= baseline else None
    else:
        state.clients = []

TELEMETRY_FIELDS = (
    'hotspot_active', 'ssid', 'band', 'security', 'interface', 'ethernet',
    'ip_gateway', 'ip_forward', 'gaming_mode', 'adblock_mode', 'stealth_mode',
    'current_dns', 'rate_limit', 'timer_info', 'clients', 'session_duration',
    'session_downloaded', 'session_uploaded', 'instant_down_bps', 'instant_up_bps',
    '_last_poll_time', '_last_rx_bytes', '_last_tx_bytes',
)


def make_poller(state):
    previous = {key: copy.deepcopy(getattr(state, key)) for key in TELEMETRY_FIELDS}

    def poll():
        sample = SimpleNamespace(**previous)
        update_telemetry(sample)
        snapshot = {key: getattr(sample, key) for key in TELEMETRY_FIELDS}
        previous.update(snapshot)
        return snapshot
    return poll


def apply_snapshot(state, snapshot):
    if (snapshot.get('interface', state.interface) != state.interface or
            (snapshot.get('hotspot_active') and state.hotspot_active is False)):
        state.traffic_history.clear()
    selected = None
    if state.clients and state.client_index < len(state.clients):
        selected = state.clients[state.client_index].get('mac')
    for key, value in snapshot.items():
        setattr(state, key, value)
    state.client_index = next((i for i, client in enumerate(state.clients) if client.get('mac') == selected),
                              min(state.client_index, max(0, len(state.clients) - 1)))
    if 'instant_down_bps' in snapshot and 'instant_up_bps' in snapshot:
        state.traffic_history.append((time.monotonic(), state.instant_down_bps, state.instant_up_bps))
    if state.modal == MODAL_KICK:
        target = state.modal_data.get('target_client', {})
        latest = next((client for client in state.clients if client.get('mac') == target.get('mac')), None)
        if latest is not None:
            state.modal_data['target_client'] = dict(latest)
        state.modal_data['associated'] = latest is not None
    state.last_refresh = time.monotonic()
    state.poll_error = ""


def drain_tasks(state):
    for kind, marker, result, callback in state.tasks.drain():
        if kind == 'job':
            state.busy_label = ""
            state.busy_mutation = False
            try:
                if marker:
                    if state.modal_data.get('running'):
                        state.modal_data.update(running=False, error=marker)
                    state.set_status(f"Error: {marker}", 2)
                else:
                    callback(result)
            except (ThinkISPError, OSError) as exc:
                state.set_status(f"Error: {exc}", 2)
            finally:
                state.tasks.finish_job()
            if state.exit_requested:
                state.running = False
        elif marker == state.tasks.generation and not state.tasks.mutating:
            if kind == 'poll':
                first = state.last_refresh is None
                apply_snapshot(state, result)
                if first and state.status_msg == 'Consultando el estado de la red…':
                    state.set_status("Red consultada. Selecciona una acción o pulsa [?] para ayuda.")
            else:
                state.poll_error = result
                state.set_status(f"No se pudo actualizar: {result}", 2)


def render_header(win, state, max_y, max_x):
    safe_addstr(win, 0, 2, "◈ thinkISP", theme.color(22) | curses.A_BOLD)
    safe_addstr(win, 0, 15, fit_text(state.hostname, max_x - 44), theme.color(23))
    if state.poll_error:
        label, color = 'DATOS SIN ACTUALIZAR', 3
    elif state.last_refresh is None:
        label, color = 'CONSULTANDO', 3
    else:
        age = time.monotonic() - state.last_refresh
        label, color = (f'SIN ACTUALIZAR {int(age)} s', 3) if age > 8 else ('● EN VIVO', 1)
    safe_addstr(win, 0, max_x - len(label) - 2, label, theme.color(color))
    safe_addstr(win, 1, 2, fit_text(f"{state.interface}  /  {state.band}", max_x - 4), curses.A_DIM)
    return 2


def render_status_bar(win, state, start_y, max_x):
    w = max_x - 2
    draw_box(win, start_y, 1, 6, w, "CONEXIÓN", curses.A_DIM)
    inner = w - 4
    left_w = max(24, inner // 2)
    right_x = 3 + left_w + 2
    right_w = max_x - right_x - 3
    if state.hotspot_active is None:
        status, color = 'Estado sin confirmar', 3
    elif state.hotspot_active:
        status, color = '● Punto de acceso activo', 1
    else:
        status, color = '○ Punto de acceso apagado', 16
    safe_addstr(win, start_y + 1, 3, fit_text(status, left_w), theme.color(color) | curses.A_BOLD)
    safe_addstr(win, start_y + 1, right_x, fit_text(f"Red: {state.ssid}", right_w), curses.A_BOLD)
    upstream = state.ethernet
    link = f"{upstream.get('interface') or 'Sin datos'} · {upstream.get('speed') or 'Sin datos'}" if upstream.get('connected') else 'Sin conexión detectada'
    safe_addstr(win, start_y + 2, 3, fit_text(f"Puerta de enlace: {state.ip_gateway}", left_w))
    safe_addstr(win, start_y + 2, right_x, fit_text(f"Salida: {link}", right_w), theme.color(7 if upstream.get('connected') else 3))
    safe_addstr(win, start_y + 3, 3, fit_text(f"DNS: {state.current_dns}", inner), theme.color(7))
    modes = f"CAKE {'activo' if state.gaming_mode else 'inactivo'}   ·   Filtro DNS {'activo' if state.adblock_mode else 'inactivo'}   ·   Red {'oculta' if state.stealth_mode else 'visible'}"
    if state.rate_limit:
        modes += f"   ·   Límite {state.rate_limit}"
    if state.last_refresh is None:
        modes = 'Leyendo configuración de la red…'
    safe_addstr(win, start_y + 4, 3, fit_text(modes, inner), curses.A_DIM)
    return start_y + 6


def render_metrics_bar(win, state, start_y, max_x):
    title = 'TRÁFICO' if state.last_refresh is None else ("TRÁFICO DE LA SESIÓN" if state.hotspot_active else "ÚLTIMA SESIÓN")
    draw_box(win, start_y, 1, 4, max_x - 2, title, curses.A_DIM)
    width = (max_x - 6) // 3
    metrics = [
        ('TIEMPO', format_duration(state.session_duration)),
        ('DESCARGA · CLIENTES', f"{format_bytes(state.instant_down_bps)}/s · {format_bytes(state.session_downloaded)}"),
        ('SUBIDA · CLIENTES', f"{format_bytes(state.instant_up_bps)}/s · {format_bytes(state.session_uploaded)}"),
    ]
    if state.last_refresh is None:
        metrics = [(label, '—') for label, _ in metrics]
    if state.timer_info:
        remaining = state.timer_info.get('remaining_str')
        timer = remaining or f"{state.timer_info.get('idle_mins', '?')} min sin clientes"
        metrics[0] = ('APAGADO PROGRAMADO', timer)
    for i, (label, value) in enumerate(metrics):
        x = 3 + i * (width + 1)
        safe_addstr(win, start_y + 1, x, fit_text(label, width), curses.A_DIM)
        safe_addstr(win, start_y + 2, x, fit_text(value, width), curses.A_BOLD | theme.color((5, 4, 1)[i]))
    return start_y + 4


def button_columns(max_x):
    return max(3, min(5, (max_x - 4) // 18))


def render_navigation(win, state, start_y, max_x):
    state.mouse_regions = [r for r in state.mouse_regions if r.get('type') != 'page']
    x = 2
    for index, (name, _) in enumerate(PAGES):
        text = f' F{index + 1} {name} '
        attr = theme.color(19) | curses.A_BOLD if index == state.page else theme.color(23)
        safe_addstr(win, start_y, x, text, attr)
        state.mouse_regions.append({'type': 'page', 'rect': (start_y, x, cell_width(text), 1), 'index': index})
        x += cell_width(text) + 1
    return start_y + 2


def render_buttons(win, state, start_y, max_x):
    state.mouse_regions = [r for r in state.mouse_regions if r.get('type') != 'button']
    actions = page_actions(state)
    state.btn_index = min(state.btn_index, len(actions) - 1)
    cols = min(button_columns(max_x), len(actions))
    btn_w = (max_x - 4) // cols
    rows = (len(actions) + cols - 1) // cols
    safe_addstr(win, start_y, 2, 'ACCIONES', curses.A_DIM)
    for idx, definition in enumerate(actions):
        label = definition['label']
        if definition['id'] == 'toggle':
            label = 'Apagar red' if state.hotspot_active else 'Encender red'
        elif definition['id'] == 'gaming':
            label = 'CAKE activo' if state.gaming_mode else 'Activar CAKE'
        elif definition['id'] == 'adblock':
            label = 'Quitar filtro' if state.adblock_mode else 'Filtrar anuncios'
        elif definition['id'] == 'stealth':
            label = 'Mostrar red' if state.stealth_mode else 'Ocultar red'
        prefix = ' › ' if state.focus == FOCUS_BUTTONS and idx == state.btn_index else '   '
        text = fit_text(prefix + label, btn_w - 5, pad=True) + ' ' + definition['hotkey'] + '  '
        focused = state.focus == FOCUS_BUTTONS and state.btn_index == idx
        attr = focus_style() if focused else theme.color(14)
        if state.busy_label:
            attr = curses.A_DIM
        x, y = 2 + idx % cols * btn_w, start_y + 1 + idx // cols
        safe_addstr(win, y, x, text, attr)
        state.mouse_regions.append({'type': 'button', 'rect': (y, x, btn_w - 1, 1), 'index': idx, 'id': definition['id']})
    return start_y + rows + 1


def sparkline(values, width):
    values = list(values)[-max(1, width):]
    if not values:
        return 'Esperando muestras…'
    peak = max(values)
    levels = '▁▂▃▄▅▆▇█'
    return ''.join(levels[min(7, int(max(0, value) / peak * 7))] if peak else levels[0] for value in values)


def render_history(win, state, y, max_x):
    draw_box(win, y, 1, 7, max_x - 2, 'TRÁFICO EN VIVO · ESCALA RELATIVA', curses.A_DIM)
    samples = list(state.traffic_history)
    seconds = samples[-1][0] - samples[0][0] if len(samples) > 1 else 0
    for row, label, index in [(1, 'Descarga', 1), (3, 'Subida', 2)]:
        values = [sample[index] for sample in samples]
        safe_addstr(win, y + row, 3, f'{label}  {format_bytes(values[-1]) + "/s" if values else "—"}', curses.A_BOLD)
        safe_addstr(win, y + row + 1, 3, sparkline(values, max_x - 7), theme.color(4 if index == 1 else 5))
    safe_addstr(win, y + 5, 3, fit_text(f'Últimos {seconds:.0f} s · pico descarga {format_bytes(max((v[1] for v in samples), default=0))}/s', max_x - 6), curses.A_DIM)
    return y + 7


def render_info_card(win, y, max_x, title, lines):
    draw_box(win, y, 1, len(lines) + 2, max_x - 2, title, curses.A_DIM)
    for row, (label, value) in enumerate(lines, y + 1):
        safe_addstr(win, row, 3, fit_text(label, 20), curses.A_DIM)
        safe_addstr(win, row, 24, fit_text(value, max_x - 28), curses.A_BOLD)
    return y + len(lines) + 2


def render_page(win, state, max_y, max_x):
    y = render_header(win, state, max_y, max_x)
    y = render_navigation(win, state, y + 1, max_x)
    if state.page == 0:
        y = render_status_bar(win, state, y, max_x)
        y = render_metrics_bar(win, state, y, max_x)
        y = render_buttons(win, state, y, max_x)
        render_clients_table(win, state, y, max_y, max_x)
    elif state.page == 1:
        y = render_buttons(win, state, y, max_x)
        render_clients_table(win, state, y, max_y, max_x)
    elif state.page == 2:
        y = render_status_bar(win, state, y, max_x)
        timer = state.timer_info or {}
        y = render_info_card(win, y, max_x, 'RED Y ACCESO', [
            ('Seguridad', state.security),
            ('Visibilidad', 'Oculta' if state.stealth_mode else 'Visible'),
            ('Apagado programado', timer.get('remaining_str') or (f"{timer['idle_mins']} min sin clientes" if timer.get('idle_mins') else 'Sin programación')),
            ('Adaptador', state.interface),
        ])
        render_buttons(win, state, y + 1, max_x)
    elif state.page == 3:
        y = render_metrics_bar(win, state, y, max_x)
        y = render_history(win, state, y, max_x)
        y = render_info_card(win, y, max_x, 'CONTROL DE TRÁFICO', [
            ('Cola CAKE', ('Activa' if state.hotspot_active else 'Guardada para encendido') if state.gaming_mode else 'Inactiva'),
            ('Límite compartido', (str(state.rate_limit) + (' · guardado' if state.hotspot_active is False else '')) if state.rate_limit else 'Sin límite'),
        ])
        render_buttons(win, state, y, max_x)
    else:
        y = render_info_card(win, y, max_x, 'RESOLUCIÓN DNS', [
            ('Proveedor actual', state.current_dns),
            ('Filtro de anuncios', 'AdGuard DNS' if state.adblock_mode else 'Desactivado'),
            ('Salida de Internet', state.ethernet.get('interface') if state.ethernet.get('connected') else 'Sin salida detectada'),
        ])
        safe_addstr(win, y + 1, 3, 'Compara proveedores o restaura los DNS anteriores.', curses.A_DIM)
        safe_addstr(win, y + 2, 3, 'El filtro se aplica a las consultas DNS; no bloquea todo anuncio.', curses.A_DIM)
        render_buttons(win, state, y + 4, max_x)
    render_footer(win, state, max_y, max_x)
    return y


def client_columns(max_x):
    columns = [('hostname', 'DISPOSITIVO', 19), ('ip', 'DIRECCIÓN IP', 15), ('signal', 'SEÑAL', 9)]
    if max_x >= 96:
        columns.append(('mac', 'DIRECCIÓN MAC', 17))
    if max_x >= 116:
        columns.append(('rate', 'TRÁFICO ↓ / ↑', 19))
    columns.append(('conn_time', 'CONECTADO', 12))
    return columns


def render_clients_table(win, state, start_y, max_y, max_x):
    state.mouse_regions = [r for r in state.mouse_regions if r.get('type') != 'client']
    height = max_y - start_y - 2
    if height < 5:
        return
    focused = state.focus == FOCUS_CLIENTS
    title = f"DISPOSITIVOS · {len(state.clients)}" if not state.poll_error else "DISPOSITIVOS · ÚLTIMA LECTURA"
    draw_box(win, start_y, 1, height, max_x - 2, title, theme.color(4) if focused else curses.A_DIM)
    columns = client_columns(max_x)
    x = 3
    for _, label, width in columns:
        safe_addstr(win, start_y + 1, x, fit_text(label, width), curses.A_DIM)
        x += width + 1
    visible = height - 3
    state.client_page_size = visible
    offset = max(0, state.client_index - visible + 1)
    if state.hotspot_active is None:
        message = 'Esperando el estado del punto de acceso…'
    elif not state.hotspot_active:
        message = 'Red apagada. [O] Encender · [C] Configurar'
    elif not state.clients:
        message = 'Sin dispositivos conectados. [R] Ver cómo conectarse'
    else:
        message = None
    if message:
        safe_addstr(win, start_y + 2, 3, fit_text(message, max_x - 6), curses.A_DIM)
        return
    for index in range(offset, min(len(state.clients), offset + visible)):
        client = state.clients[index]
        signal = client.get('signal')
        values = {
            'hostname': ('[Bloq.] ' if client.get('blocked') else '') + (client.get('hostname') or 'Sin nombre'),
            'ip': client.get('ip') or 'Sin dirección',
            'mac': client.get('mac') or 'Sin datos',
            'signal': f'{signal} dBm' if signal is not None else 'Sin datos',
            'rate': ' / '.join(format_bytes(client[key]) + '/s' if client.get(key) is not None else '—' for key in ('down_bps', 'up_bps')),
            'conn_time': format_duration(client['conn_time']) if client.get('conn_time') is not None else '—',
        }
        y = start_y + 2 + index - offset
        selected = focused and state.client_index == index
        if selected:
            safe_addstr(win, y, 2, ' ' * (max_x - 4), theme.color(8))
        x = 3
        for key, _, width in columns:
            attr = focus_style() if selected else theme.color(21 if index % 2 else 7)
            if key == 'signal' and not selected:
                strength = client.get('signal')
                attr = theme.color(1 if strength is not None and strength >= -60 else 3 if strength is not None and strength >= -75 else 2 if strength is not None else 16)
            safe_addstr(win, y, x, fit_text(values[key], width, pad=True), attr)
            x += width + 1
        state.mouse_regions.append({'type': 'client', 'rect': (y, 2, max_x - 4, 1), 'index': index})
    if len(state.clients) > visible:
        counter = f' {offset + 1}–{min(offset + visible, len(state.clients))} de {len(state.clients)} '
        safe_addstr(win, start_y + height - 1, max_x - len(counter) - 4, counter, curses.A_DIM)


def render_footer(win, state, max_y, max_x):
    if state.busy_label:
        spinner = '|/−\\'[int(time.monotonic() * 5) % 4]
        message, attr = f"{spinner} {state.busy_label}…" + (' Se cerrará al terminar.' if state.exit_requested else ''), theme.color(4) | curses.A_BOLD
    elif time.time() - state.status_msg_time < 8:
        message, attr = state.status_msg, state.status_msg_color
    elif state.poll_error:
        message, attr = f'Actualización fallida: {state.poll_error}', theme.color(3)
    elif state.focus == FOCUS_CLIENTS:
        message, attr = '[Enter] Ver dispositivo y controlar su acceso', theme.color(4)
    else:
        message, attr = page_actions(state)[state.btn_index]['hint'], curses.A_DIM
    safe_addstr(win, max_y - 2, 2, fit_text(message, max_x - 4), attr)
    help_text = '/ Acciones   F1–F5 Vistas   Tab Foco   Enter Abrir   ? Ayuda   Q Salir'
    safe_addstr(win, max_y - 1, 2, fit_text(help_text, max_x - 4), curses.A_DIM)


# =====================================================================
# MODALES EMERGENTES (POP-UPS)
# =====================================================================

def command_matches(query):
    def normalized(value):
        return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c))
    words = normalized(query.strip().lstrip('/')).split()
    options = [{'kind': 'page', 'id': i, 'label': name, 'hint': 'Ir a la vista ' + name, 'key': f'F{i + 1}'}
               for i, (name, _) in enumerate(PAGES)]
    options += [{'kind': 'action', 'id': item['id'], 'label': item['label'], 'hint': item['hint'], 'key': item['hotkey']}
                for item in BUTTON_DEFS]
    return [item for item in options if all(word in normalized(item['label'] + ' ' + item['hint']) for word in words)]


def render_commands_modal(win, state, max_y, max_x):
    data = state.modal_data
    query = data.get('query', '')
    options = command_matches(query)
    width, height = min(82, max_x - 6), min(max(10, min(12, len(options)) + 8), max_y - 4)
    y, x = modal_frame(win, max_y, max_x, height, width, 'BUSCAR ACCIONES', anchor_y=3)
    modal_line(win, y + 2, x, width, fit_text(' › ' + query + '▏', width - 6, pad=True), theme.color(14) | curses.A_BOLD)
    options = command_matches(query)
    selected = min(data.get('selected_idx', 0), max(0, len(options) - 1))
    size = height - 8
    offset = max(0, selected - size + 1)
    state.modal_regions = []
    if not options:
        modal_line(win, y + 5, x, width, 'Sin resultados. Prueba con red, DNS o límite.', theme.color(16))
    for index in range(offset, min(len(options), offset + size)):
        item = options[index]
        row = y + 4 + index - offset
        label = (' › ' if index == selected else '   ') + item['label']
        style = focus_style() if index == selected else theme.color(7)
        modal_line(win, row, x, width, fit_text(label, width - 6, pad=True), style)
        safe_addstr(win, row, x + width - 8, fit_text(item['key'], 4), style)
        state.modal_regions.append({'rect': (row, x + 3, width - 6, 1), 'field': 'selected_idx', 'value': index})
    if options:
        modal_line(win, y + height - 4, x, width, options[selected]['hint'], theme.color(16))
    modal_line(win, y + height - 2, x, width, '↑↓ Elegir   Enter Abrir   Esc Cerrar', theme.color(4))


def render_qr_modal(win, state, max_y, max_x):
    data = state.modal_data
    lines = data.get('qr_lines', [])
    qr_width = max((cell_width(line) for line in lines), default=0)
    fits = qr_width + 6 <= max_x - 4 and len(lines) + 9 <= max_y - 2
    visible_lines = lines if fits else []
    width = min(max(60, qr_width + 6 if fits else 60), max_x - 4)
    height = len(visible_lines) + 9 if visible_lines else 12
    y, x = modal_frame(win, max_y, max_x, height, width, 'CONECTAR UN DISPOSITIVO')
    modal_controls(win, state, y + height - 2, x, width)
    if data.get('running'):
        safe_addstr(win, y + 3, x + 4, 'Preparando código y credenciales…', curses.A_DIM)
        return
    if data.get('error'):
        safe_addstr(win, y + 3, x + 4, fit_text(data['error'], width - 8), theme.color(2))
        return
    row = y + 2
    for line in visible_lines:
        qr_x = x + (width - qr_width) // 2
        safe_addstr(win, row, qr_x, line.ljust(qr_width), theme.color(15))
        row += 1
    if not visible_lines:
        note = data.get('note') or 'Amplía la terminal para ver el QR completo.'
        safe_addstr(win, row, x + 4, fit_text(note, width - 8), theme.color(3))
        row += 2
    safe_addstr(win, row, x + 4, fit_text(f"Red: {data.get('ssid', state.ssid)}", width - 8), curses.A_BOLD)
    safe_addstr(win, row + 1, x + 4, fit_text(f"Contraseña: {data.get('psk', '')}", width - 8), curses.A_BOLD)
    safe_addstr(win, row + 2, x + 4, fit_text(f"Seguridad: {data.get('security', state.security)}", width - 8), curses.A_DIM)


def modal_frame(win, max_y, max_x, height, width, title, anchor_y=None):
    y = max(1, (max_y - height) // 2) if anchor_y is None else min(anchor_y, max_y - height)
    x = max(2, (max_x - width) // 2)
    for row in range(height):
        safe_addstr(win, y + row, x, ' ' * width, theme.color(13))
    draw_box(win, y, x, height, width, title, theme.color(4))
    return y, x


def modal_line(win, y, x, width, text, attr=0):
    safe_addstr(win, y, x + 3, fit_text(text, width - 6), attr)


def modal_controls(win, state, y, x, width, apply_text=None):
    if apply_text:
        label = f' Enter {apply_text} '
        safe_addstr(win, y, x + 3, label, focus_style())
        state.modal_regions.append({'rect': (y, x + 3, cell_width(label), 1), 'key': 10})
    label = ' Esc Volver ' if apply_text else ' Esc Cerrar '
    bx = x + width - len(label) - 3
    safe_addstr(win, y, bx, label, theme.color(7) | curses.A_DIM)
    state.modal_regions.append({'rect': (y, bx, len(label), 1), 'key': 27})


def render_timer_modal(win, state, max_y, max_x):
    width, height = 66, 19
    y, x = modal_frame(win, max_y, max_x, height, width, 'APAGADO AUTOMÁTICO')
    timer = state.timer_info or {}
    current = timer.get('remaining_str') or (f"{timer['idle_mins']} min sin clientes" if timer.get('idle_mins') else 'Sin programación')
    modal_line(win, y + 2, x, width, f'Actual: {current}', curses.A_BOLD)
    labels = ['Dentro de 15 minutos', 'Dentro de 30 minutos', 'Dentro de 45 minutos',
              'Dentro de 1 hora', 'Dentro de 2 horas', 'Después de 10 minutos sin clientes', 'Quitar programación']
    selected = state.modal_data.get('preset_idx', 1)
    custom = state.modal_data.get('custom_text', '')
    for index, label in enumerate(labels):
        attr = focus_style() if index == selected and not custom else theme.color(7)
        modal_line(win, y + 4 + index, x, width, fit_text(' ' + label, width - 6, pad=True), attr)
        state.modal_regions.append({'rect': (y + 4 + index, x + 3, width - 6, 1), 'field': 'preset_idx', 'value': index})
    modal_line(win, y + 12, x, width, 'Personalizado: ' + (custom + '▏' if custom else 'escribe 1h20m o idle 10'), theme.color(4))
    modal_line(win, y + 14, x, width, '↑↓ Elegir · escribe para personalizar · Enter Aplicar', curses.A_DIM)
    modal_controls(win, state, y + 17, x, width, 'Aplicar')


def render_speedtest_modal(win, state, max_y, max_x):
    width, height = 64, 16
    y, x = modal_frame(win, max_y, max_x, height, width, 'PRUEBA DE CONEXIÓN')
    data = state.modal_data
    running = data.get('running', False)
    error = data.get('error')
    summary = 'Midiendo conexión y transferencia…' if running else ('La prueba terminó con errores.' if error else 'Prueba completada.')
    modal_line(win, y + 2, x, width, summary, theme.color(3 if running or error else 1) | curses.A_BOLD)
    modal_line(win, y + 4, x, width, 'Conexión HTTP', curses.A_DIM)
    modal_line(win, y + 5, x, width, f"{data['ping_ms']:.1f} ms" if data.get('ping_ms') is not None else '—', curses.A_BOLD)
    for row, label, key in [(7, 'Descarga', 'down_mbps'), (9, 'Subida', 'up_mbps')]:
        value = f"{data[key]:.1f} Mbps" if data.get(key) is not None else '—'
        modal_line(win, y + row, x, width, f'{label:<18}{value}', curses.A_BOLD)
    modal_line(win, y + 11, x, width, 'Cloudflare · descarga 25 MB / subida 5 MB', curses.A_DIM)
    if error:
        modal_line(win, y + 12, x, width, str(error), theme.color(2))
    modal_controls(win, state, y + 14, x, width)
    if running:
        modal_line(win, y + 12, x, width, 'Puedes cerrar el diálogo mientras se mide.', curses.A_DIM)
    elif not error:
        modal_line(win, y + 12, x, width, 'R Repetir medición · consume otros 30 MB', curses.A_DIM)


def render_dns_modal(win, state, max_y, max_x):
    width, height = 70, 16
    y, x = modal_frame(win, max_y, max_x, height, width, 'SERVIDORES DNS')
    modal_line(win, y + 2, x, width, 'PROVEEDOR                  SERVIDOR          RESPUESTA', curses.A_DIM)
    data = state.modal_data
    if data.get('running'):
        modal_line(win, y + 5, x, width, 'Comparando respuesta de los proveedores…', theme.color(4))
    elif data.get('error'):
        modal_line(win, y + 5, x, width, str(data['error']), theme.color(2))
    else:
        results = data.get('results', [])
        offset = max(0, data.get('selected_idx', 0) - (height - 9) + 1)
        for i in range(offset, min(len(results), offset + height - 9)):
            item = results[i]
            delay = '—' if item['key'] == 'restore' else (f"{item['time']} ms" if item.get('time') is not None else 'Sin respuesta')
            row = f"{fit_text(item['name'], 26, pad=True)} {fit_text(item['ip'], 17, pad=True)} {delay}"
            attr = focus_style() if i == data.get('selected_idx', 0) else theme.color(7)
            modal_line(win, y + 4 + i - offset, x, width, row, attr)
            state.modal_regions.append({'rect': (y + 4 + i - offset, x + 3, width - 6, 1), 'field': 'selected_idx', 'value': i})
    modal_line(win, y + 12, x, width, fit_text(f'Actual: {state.current_dns} · R Comparar otra vez', width - 6), curses.A_DIM)
    modal_controls(win, state, y + 14, x, width, None if data.get('running') or data.get('error') else 'Aplicar')


def render_limit_modal(win, state, max_y, max_x):
    width, height = 66, 17
    y, x = modal_frame(win, max_y, max_x, height, width, 'LÍMITE COMPARTIDO')
    modal_line(win, y + 2, x, width, f"Actual: {state.rate_limit or 'Sin límite'}", curses.A_BOLD)
    labels = ['5 Mbps', '15 Mbps', '30 Mbps', '50 Mbps', '100 Mbps', 'Sin límite']
    custom = state.modal_data.get('custom_text', '')
    for index, label in enumerate(labels):
        attr = focus_style() if index == state.modal_data.get('preset_idx', 2) and not custom else theme.color(7)
        modal_line(win, y + 4 + index, x, width, fit_text(' ' + label, width - 6, pad=True), attr)
        state.modal_regions.append({'rect': (y + 4 + index, x + 3, width - 6, 1), 'field': 'preset_idx', 'value': index})
    modal_line(win, y + 11, x, width, 'Personalizado: ' + (custom + ' Mbps ▏' if custom else 'escribe una velocidad en Mbps'), theme.color(4))
    modal_line(win, y + 12, x, width, state.modal_data.get('error') or '↑↓ Elegir · Enter Aplicar · Sustituye las colas CAKE.',
               theme.color(2) if state.modal_data.get('error') else curses.A_DIM)
    modal_controls(win, state, y + 15, x, width, 'Aplicar')


def render_blocked_modal(win, state, max_y, max_x):
    width, height = min(70, max_x - 4), min(18, max_y - 2)
    y, x = modal_frame(win, max_y, max_x, height, width, 'ACCESO BLOQUEADO')
    data = state.modal_data
    if data.get('running'):
        modal_line(win, y + 3, x, width, 'Consultando bloqueos guardados…', curses.A_DIM)
    elif data.get('error'):
        modal_line(win, y + 3, x, width, data['error'], theme.color(2))
    else:
        clients = data.get('clients', [])
        selected = data.get('selected_idx', 0)
        size = height - 7
        offset = max(0, selected - size + 1)
        if not clients:
            modal_line(win, y + 3, x, width, 'No hay dispositivos bloqueados.', curses.A_DIM)
        for index in range(offset, min(len(clients), offset + size)):
            modal_line(win, y + 3 + index - offset, x, width, fit_text(clients[index], width - 6, pad=True),
                       focus_style() if index == selected else theme.color(7))
            state.modal_regions.append({'rect': (y + 3 + index - offset, x + 3, width - 6, 1), 'field': 'selected_idx', 'value': index})
    modal_line(win, y + height - 3, x, width, 'R Actualizar · incluye clientes desconectados.', curses.A_DIM)
    modal_controls(win, state, y + height - 2, x, width,
                   'Permitir acceso' if data.get('clients') and not data.get('running') else None)


def render_kick_modal(win, state, max_y, max_x):
    width, height = 70, 18
    y, x = modal_frame(win, max_y, max_x, height, width, 'DISPOSITIVO · DETALLES Y ACCESO')
    client = state.modal_data.get('target_client', {})
    for row, label, key in [(2, 'Nombre', 'hostname'), (4, 'Dirección IP', 'ip'), (5, 'Dirección MAC', 'mac')]:
        modal_line(win, y + row, x, width, f"{label:<17}{client.get(key) or 'Sin datos'}", curses.A_BOLD if row == 2 else 0)
    signal = client.get('signal')
    modal_line(win, y + 6, x, width, f"Señal            {str(signal) + ' dBm' if signal is not None else 'Sin datos'}")
    duration = format_duration(client['conn_time']) if client.get('conn_time') is not None else 'Sin datos'
    modal_line(win, y + 7, x, width, f'Conectado        {duration}')
    link = ' / '.join(f'{client[key]:.0f}' if client.get(key) is not None else '—' for key in ('tx_mbps', 'rx_mbps'))
    traffic = ' / '.join(format_bytes(client[key]) + '/s' if client.get(key) is not None else '—' for key in ('down_bps', 'up_bps'))
    modal_line(win, y + 8, x, width, f'Enlace TX / RX   {link} Mbps')
    modal_line(win, y + 9, x, width, f'Tráfico ↓ / ↑    {traffic}')
    if state.modal_data.get('associated') is False:
        modal_line(win, y + 3, x, width, 'Desconectado · última información recibida', curses.A_DIM)
    blocked = client.get('blocked', False)
    modal_line(win, y + 11, x, width, 'Acceso bloqueado' if blocked else 'Acceso permitido', theme.color(3 if blocked else 1) | curses.A_BOLD)
    modal_line(win, y + 12, x, width, 'Bloquear impide acceso al equipo e Internet.', curses.A_DIM)
    modal_line(win, y + 13, x, width, 'El dispositivo puede seguir asociado al Wi-Fi.', curses.A_DIM)
    modal_controls(win, state, y + 16, x, width, 'Permitir acceso' if blocked else 'Bloquear acceso')


def render_help_modal(win, state, max_y, max_x):
    width, height = min(74, max_x - 4), 22
    y, x = modal_frame(win, max_y, max_x, height, width, 'CONTROLES DEL PANEL')
    lines = [
        ('F1–F5 / [ ]', 'Cambiar de vista'),
        ('Tab / Shift+Tab', 'Cambiar foco o recorrer acciones'),
        ('Flechas', 'Mover la selección'),
        ('Enter', 'Abrir acción o ver dispositivo'),
        ('/ / Ctrl+P', 'Buscar cualquier acción por nombre'),
        ('O', 'Encender o apagar el punto de acceso'),
        ('C / N', 'Configurar red / plan de conectividad'),
        ('R', 'Ver credenciales y código QR'),
        ('T', 'Programar apagado'),
        ('G / L', 'Colas CAKE / límite (admite valor propio)'),
        ('D / A / E', 'Comparar DNS / filtro / restaurar DNS'),
        ('S', 'Ocultar o mostrar la red'),
        ('P / B', 'Prueba de conexión / optimizar Wi-Fi'),
        ('K / U', 'Ver dispositivo / lista de bloqueados'),
        ('Esc', 'Cerrar diálogo sin aplicar cambios'),
        ('Q', 'Salir del panel; conserva el estado de la red'),
        ('Ratón', 'Clic en pestañas, acciones y dispositivos'),
    ]
    for i, (key, explanation) in enumerate(lines):
        modal_line(win, y + 2 + i, x, width, f'{key:<18}{explanation}', curses.A_DIM if i % 2 else 0)
    modal_controls(win, state, y + height - 2, x, width)


# =====================================================================
# EJECUCIÓN DE ACCIONES
# =====================================================================
def checked_result(result):
    ok, message = result
    if not ok:
        raise ThinkISPError(str(message))
    return message or 'Operación completada.'


def submit_action(state, label, operation, callback=None, mutation=True):
    if state.busy_label:
        state.set_status('Hay una operación en curso. Puedes seguir navegando.', 3)
        return False
    callback = callback or (lambda message: state.set_status(str(message), 1))
    if state.tasks is None:
        # También permite usar los controladores sin iniciar el bucle curses.
        callback(operation())
        return True
    if state.tasks.submit(operation, callback, mutation=mutation):
        state.busy_label = label
        state.busy_mutation = mutation
        return True
    return False


def close_modal(state):
    state.modal = MODAL_NONE
    state.modal_data = {}
    state.psk = ''


def modal_task(state, modal, label, operation):
    if state.busy_label:
        state.set_status('Espera a que termine la operación actual.', 3)
        return
    state.modal = modal
    data = {'running': True}
    state.modal_data = data

    def complete(result):
        if state.modal == modal and state.modal_data is data:
            data.update(result)
            data['running'] = False
        state.set_status(f'{label}: listo.', 1)

    submit_action(state, label, operation, complete, mutation=False)


def prepare_qr():
    details = hotspot_details(secrets=True)
    lines, note = [], ''
    if shutil.which('qrencode'):
        payload = wifi_qr_payload(details['ssid'], details['psk'], details['hidden'], details['security'])
        result = run_cmd(['qrencode', '-t', 'UTF8', payload], check=True, strip=False)
        lines = result.splitlines()
    else:
        note = 'Instala qrencode para mostrar el código QR.'
    return {**details, 'qr_lines': lines, 'note': note}


def _execute_button_action(btn_id, state):
    if btn_id == 'quit':
        if state.busy_label and state.busy_mutation:
            state.exit_requested = True
            state.set_status('El panel se cerrará al terminar la operación en curso.', 3)
        else:
            state.running = False
        return
    if btn_id == 'help':
        state.modal, state.modal_data = MODAL_HELP, {}
        return
    if state.busy_label:
        state.set_status('Hay una operación en curso. Puedes seguir navegando.', 3)
        return
    if btn_id == 'toggle':
        if state.hotspot_active is None:
            state.set_status('Espera a que se confirme el estado del punto de acceso.', 3)
            return
        active = state.hotspot_active
        def toggle():
            if active:
                checked_result(stop_hotspot())
                stop_daemon()
                return 'Punto de acceso apagado; red anterior restaurada.'
            message = checked_result(start_hotspot())
            start_daemon()
            return message
        submit_action(state, 'Apagando la red' if active else 'Encendiendo la red', toggle)
    elif btn_id == 'boost':
        submit_action(state, 'Aplicando optimización', lambda: checked_result(apply_boost_mode()))
    elif btn_id == 'gaming':
        enabled = not state.gaming_mode
        submit_action(state, 'Configurando colas CAKE', lambda: checked_result(set_gaming_mode(enabled)))
    elif btn_id == 'adblock':
        enabled = not state.adblock_mode
        submit_action(state, 'Configurando filtro DNS', lambda: checked_result(set_adblock(enabled)))
    elif btn_id == 'stealth':
        enabled = not state.stealth_mode
        def change_hidden():
            if not set_stealth_mode(enabled):
                raise ThinkISPError('No se confirmó la visibilidad de la red.')
            return 'Nombre de red oculto.' if enabled else 'Nombre de red visible.'
        submit_action(state, 'Configurando visibilidad', change_hidden)
    elif btn_id == 'timer':
        state.modal, state.modal_data = MODAL_TIMER, {'preset_idx': 1, 'custom_text': ''}
    elif btn_id == 'limit':
        state.modal, state.modal_data = MODAL_LIMIT, {'preset_idx': 2}
    elif btn_id == 'dns':
        def compare_dns():
            results = benchmark_dns()
            results.append({'key': 'restore', 'name': 'Restaurar DNS anterior', 'ip': 'Sistema', 'time': None})
            return {'results': results, 'selected_idx': 0}
        modal_task(state, MODAL_DNS, 'Comparando servidores DNS', compare_dns)
    elif btn_id == 'speed':
        def measure():
            return {**run_speedtest(), 'phase': 'completado'}
        modal_task(state, MODAL_SPEEDTEST, 'Midiendo conexión y transferencia', measure)
    elif btn_id == 'qr':
        modal_task(state, MODAL_QR, 'Preparando conexión QR', prepare_qr)
    elif btn_id == 'access':
        if state.clients and 0 <= state.client_index < len(state.clients):
            state.modal = MODAL_KICK
            state.modal_data = {'target_client': dict(state.clients[state.client_index])}
        else:
            state.set_status('No hay dispositivos conectados para consultar.', 3)
    elif btn_id == 'blocked':
        modal_task(state, MODAL_BLOCKED, 'Consultando bloqueos',
                   lambda: {'clients': get_blocked_clients(), 'selected_idx': 0})
    elif btn_id == 'restore_dns':
        submit_action(state, 'Restaurando DNS anterior', lambda: checked_result(set_dns_provider('restore')))
    elif btn_id == 'network_plan':
        from thinkisp.ui.connectivity import configure_connectivity
        if state.tasks:
            state.tasks.pause(True)
        try:
            saved = configure_connectivity(screen=state.screen)
            state.set_status('Plan guardado; no aplicado a la red.' if saved else 'Plan sin cambios.', 4)
        finally:
            if state.screen is not None:
                state.screen.timeout(100)
                state.screen.keypad(True)
                state.screen.clear()
                init_colors()
                theme.background(state.screen)
            if state.tasks:
                state.tasks.pause(False)
    elif btn_id == 'config':
        if state.tasks:
            state.tasks.pause(True)
        try:
            configure_hotspot(screen=state.screen)
            state.set_status('Configuración guardada.', 1)
        except (KeyboardInterrupt, EOFError):
            state.set_status('Configuración cancelada.', 3)
        finally:
            if state.screen is not None:
                state.screen.timeout(100)
                state.screen.keypad(True)
                state.screen.clear()
                init_colors()
                theme.background(state.screen)
            if state.tasks:
                state.tasks.pause(False)


def execute_button_action(btn_id, state):
    try:
        _execute_button_action(btn_id, state)
    except (ThinkISPError, OSError) as exc:
        state.set_status(f"Error: {exc}", 2)


# =====================================================================
# MANEJADOR DE ENTRADA POR RATÓN
# =====================================================================
def handle_mouse(my, mx, state, activate=False):
    """Procesa eventos de clic del ratón sobre botones o filas de clientes."""
    if state.modal != MODAL_NONE:
        for region in state.modal_regions:
            y, x, width, height = region['rect']
            if y <= my < y + height and x <= mx < x + width:
                if 'key' in region:
                    handle_keypress(region['key'], state, state.viewport_width)
                else:
                    state.modal_data[region['field']] = region['value']
                    if region['field'] == 'preset_idx':
                        state.modal_data['custom_text'] = ''
                    if activate and state.modal == MODAL_COMMANDS:
                        handle_keypress(10, state, state.viewport_width)
                break
        return

    for reg in state.mouse_regions:
        y, x, w, h = reg["rect"]
        if y <= my < y + h and x <= mx < x + w:
            rtype = reg.get("type")
            if rtype == "page":
                select_page(state, reg["index"])
                return
            if rtype == "button":
                state.focus = FOCUS_BUTTONS
                state.btn_index = reg["index"]
                execute_button_action(reg["id"], state)
                return
            elif rtype == "client":
                state.focus = FOCUS_CLIENTS
                state.client_index = reg["index"]
                if activate:
                    state.modal = MODAL_KICK
                    state.modal_data = {"target_client": dict(state.clients[state.client_index])}
                return


# =====================================================================
# MANEJADOR DE EVENTOS DE TECLADO
# =====================================================================
def handle_keypress(ch, state, max_x):
    """Procesa la entrada del teclado considerando foco y modales."""
    # 1. Manejo cuando hay un modal abierto
    if state.modal != MODAL_NONE:
        if ch == 27 or (state.modal != MODAL_COMMANDS and ch in (ord('q'), ord('Q'))):
            close_modal(state)
            return

        if state.modal == MODAL_COMMANDS:
            options = command_matches(state.modal_data.get('query', ''))
            selected = state.modal_data.get('selected_idx', 0)
            if ch == curses.KEY_UP:
                state.modal_data['selected_idx'] = max(0, selected - 1)
            elif ch == curses.KEY_DOWN:
                state.modal_data['selected_idx'] = min(max(0, len(options) - 1), selected + 1)
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                state.modal_data['query'] = state.modal_data.get('query', '')[:-1]
                state.modal_data['selected_idx'] = 0
            elif ch == 21:
                state.modal_data.update(query='', selected_idx=0)
            elif ch in (10, 13) and options:
                chosen = options[min(selected, len(options) - 1)]
                close_modal(state)
                if chosen['kind'] == 'page':
                    select_page(state, chosen['id'])
                else:
                    execute_button_action(chosen['id'], state)
            elif 32 <= ch <= 126:
                state.modal_data['query'] = (state.modal_data.get('query', '') + chr(ch))[:80]
                state.modal_data['selected_idx'] = 0
            return

        if ch in (ord('r'), ord('R')) and state.modal in (MODAL_DNS, MODAL_SPEEDTEST, MODAL_BLOCKED):
            action = {MODAL_DNS: 'dns', MODAL_SPEEDTEST: 'speed', MODAL_BLOCKED: 'blocked'}[state.modal]
            if not state.busy_label:
                close_modal(state)
                execute_button_action(action, state)
            return

        if state.modal in (MODAL_QR, MODAL_HELP):
            if ch in (10, 13, 32):  # Enter o Espacio
                close_modal(state)
            return

        elif state.modal == MODAL_TIMER:
            presets = ["15m", "30m", "45m", "1h", "2h", "idle 10", "cancel"]
            cur_p = state.modal_data.get("preset_idx", 0)

            if ch in (curses.KEY_LEFT, curses.KEY_UP):
                state.modal_data["custom_text"] = ""
                state.modal_data["preset_idx"] = max(0, cur_p - 1)
            elif ch in (curses.KEY_RIGHT, curses.KEY_DOWN):
                state.modal_data["custom_text"] = ""
                state.modal_data["preset_idx"] = min(len(presets) - 1, cur_p + 1)
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                cust = state.modal_data.get("custom_text", "")
                if cust:
                    state.modal_data["custom_text"] = cust[:-1]
            elif 32 <= ch <= 126:
                # Escribir en texto manual
                cust = state.modal_data.get("custom_text", "")
                if len(cust) < 15:
                    state.modal_data["custom_text"] = cust + chr(ch)
            elif ch in (10, 13):  # Enter
                custom = state.modal_data.get('custom_text', '').strip()
                value = custom or presets[cur_p]
                if value == 'cancel':
                    operation = cancel_timer
                elif value.startswith('idle'):
                    parts = value.split()
                    if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) <= 0:
                        state.set_status('Usa idle seguido de minutos positivos, por ejemplo idle 10.', 3)
                        return
                    minutes = int(parts[1])
                    operation = lambda: set_timer_idle(minutes)
                else:
                    seconds = parse_duration_str(value)
                    if seconds <= 0:
                        state.set_status('Indica un tiempo válido: 15m, 90 o 1h20m.', 3)
                        return
                    operation = lambda: set_timer_countdown(seconds)
                if submit_action(state, 'Configurando temporizador', lambda: checked_result(operation())):
                    close_modal(state)
            return

        elif state.modal == MODAL_LIMIT:
            presets = ['5', '15', '30', '50', '100', 'reset']
            selected = state.modal_data.get('preset_idx', 2)
            if ch in (curses.KEY_LEFT, curses.KEY_UP):
                state.modal_data.update(preset_idx=max(0, selected - 1), custom_text='')
            elif ch in (curses.KEY_RIGHT, curses.KEY_DOWN):
                state.modal_data.update(preset_idx=min(len(presets) - 1, selected + 1), custom_text='')
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                state.modal_data['custom_text'] = state.modal_data.get('custom_text', '')[:-1]
            elif ch in range(ord('0'), ord('9') + 1) or ch == ord('.'):
                text = state.modal_data.get('custom_text', '')
                if len(text) < 12:
                    state.modal_data['custom_text'] = text + chr(ch)
                state.modal_data.pop('error', None)
            elif ch in (10, 13):
                value = state.modal_data.get('custom_text', '') or presets[selected]
                if value != 'reset':
                    import math
                    try:
                        rate = float(value)
                    except ValueError:
                        rate = 0
                    if not math.isfinite(rate) or rate <= 0:
                        state.modal_data['error'] = 'Escribe una velocidad mayor que cero.'
                        state.set_status('Escribe una velocidad mayor que cero.', 3)
                        return
                operation = clear_rate_limit if value == 'reset' else lambda: set_rate_limit(value)
                if submit_action(state, 'Configurando límite', lambda: checked_result(operation())):
                    close_modal(state)
            return

        elif state.modal == MODAL_BLOCKED:
            clients = state.modal_data.get('clients', [])
            selected = state.modal_data.get('selected_idx', 0)
            if ch == curses.KEY_UP:
                state.modal_data['selected_idx'] = max(0, selected - 1)
            elif ch == curses.KEY_DOWN:
                state.modal_data['selected_idx'] = min(max(0, len(clients) - 1), selected + 1)
            elif ch in (10, 13) and clients and not state.modal_data.get('running'):
                mac = clients[selected]
                if submit_action(state, 'Permitiendo acceso del dispositivo', lambda: checked_result(unkick_client(mac))):
                    close_modal(state)
            return

        elif state.modal == MODAL_DNS:
            results = state.modal_data.get("results", [])
            cur_s = state.modal_data.get("selected_idx", 0)
            if ch == curses.KEY_UP:
                state.modal_data["selected_idx"] = max(0, cur_s - 1)
            elif ch == curses.KEY_DOWN:
                state.modal_data["selected_idx"] = min(len(results) - 1, cur_s + 1)
            elif ch in (10, 13):  # Enter
                if results and 0 <= cur_s < len(results):
                    chosen = results[cur_s]
                    if submit_action(state, 'Aplicando DNS', lambda: checked_result(set_dns_provider(chosen['key']))):
                        close_modal(state)
            return

        elif state.modal == MODAL_KICK:
            if ch in (ord('k'), ord('K'), 10, 13):  # Confirmar cambio de acceso
                target = state.modal_data.get("target_client", {})
                mac = target.get("mac")
                if mac:
                    blocked = target.get('blocked', False)
                    operation = (lambda: unkick_client(mac)) if blocked else (lambda: kick_client(mac))
                    if submit_action(state, 'Actualizando acceso del dispositivo', lambda: checked_result(operation())):
                        close_modal(state)
            return

        elif state.modal == MODAL_SPEEDTEST:
            if ch in (10, 13):  # Enter para cerrar cuando termine
                if not state.modal_data.get("running"):
                    close_modal(state)
            return

    if ch in (ord('/'), 16):
        state.modal, state.modal_data = MODAL_COMMANDS, {'query': '', 'selected_idx': 0}
        return

    if ch in range(curses.KEY_F1, curses.KEY_F5 + 1):
        select_page(state, ch - curses.KEY_F1)
        return
    if ch in (ord('['), ord(']')):
        select_page(state, state.page + (1 if ch == ord(']') else -1))
        return

    # 2. Navegación y Atajos Globales (sin modal activo)
    # Atajos de una sola letra
    key_map = {
        ord('n'): 'network_plan', ord('N'): 'network_plan',
        ord('u'): 'blocked', ord('U'): 'blocked',
        ord('e'): 'restore_dns', ord('E'): 'restore_dns',
        ord('c'): "config", ord('C'): "config",
        ord('b'): "boost", ord('B'): "boost",
        ord('g'): "gaming", ord('G'): "gaming",
        ord('a'): "adblock", ord('A'): "adblock",
        ord('s'): "stealth", ord('S'): "stealth",
        ord('t'): "timer", ord('T'): "timer",
        ord('l'): "limit", ord('L'): "limit",
        ord('d'): "dns", ord('D'): "dns",
        ord('p'): "speed", ord('P'): "speed",
        ord('r'): "qr", ord('R'): "qr",
        ord('o'): "toggle", ord('O'): "toggle",
        ord('?'): "help", ord('h'): "help", ord('H'): "help",
        ord('q'): "quit", ord('Q'): "quit",
    }

    if ch in key_map:
        execute_button_action(key_map[ch], state)
        return

    # Ver el dispositivo seleccionado antes de cambiar su acceso.
    if ch in (ord('k'), ord('K')):
        if state.clients and 0 <= state.client_index < len(state.clients):
            state.modal = MODAL_KICK
            state.modal_data = {"target_client": dict(state.clients[state.client_index])}
        else:
            state.set_status("Selecciona un dispositivo conectado para ver su acceso.", 3)
        return

    # Tecla Tab (cambiar foco entre Botones y Clientes)
    if ch in (9, curses.KEY_BTAB) and state.page in (0, 1):  # Tab o Shift-Tab
        state.focus = FOCUS_CLIENTS if state.focus == FOCUS_BUTTONS else FOCUS_BUTTONS
        return

    # Flechas de navegación según panel enfocado
    if state.focus == FOCUS_BUTTONS:
        actions = page_actions(state)
        cols = min(button_columns(max_x), len(actions))
        total_btns = len(actions)

        if ch in (curses.KEY_LEFT, curses.KEY_BTAB):
            state.btn_index = (state.btn_index - 1) % total_btns
        elif ch in (curses.KEY_RIGHT, 9):
            state.btn_index = (state.btn_index + 1) % total_btns
        elif ch == curses.KEY_UP:
            if state.btn_index >= cols:
                state.btn_index -= cols
        elif ch == curses.KEY_DOWN:
            if state.btn_index + cols < total_btns:
                state.btn_index += cols
            else:
                # Saltar al panel de clientes
                if state.clients and state.page in (0, 1):
                    state.focus = FOCUS_CLIENTS
        elif ch in (10, 13, 32):  # Enter o Espacio
            execute_button_action(actions[state.btn_index]["id"], state)

    elif state.focus == FOCUS_CLIENTS:
        total_c = len(state.clients)
        if ch == curses.KEY_UP:
            if state.client_index > 0:
                state.client_index -= 1
            else:
                state.focus = FOCUS_BUTTONS
        elif ch == curses.KEY_DOWN:
            if state.client_index < total_c - 1:
                state.client_index += 1
        elif ch == curses.KEY_NPAGE:
            state.client_index = min(total_c - 1, state.client_index + max(1, state.client_page_size)) if total_c else 0
        elif ch == curses.KEY_PPAGE:
            state.client_index = max(0, state.client_index - max(1, state.client_page_size))
        elif ch == curses.KEY_HOME:
            state.client_index = 0
        elif ch == curses.KEY_END:
            state.client_index = max(0, total_c - 1)
        elif ch in (10, 13):  # Ver dispositivo y confirmar bloqueo
            if state.clients:
                state.modal = MODAL_KICK
                state.modal_data = {"target_client": dict(state.clients[state.client_index])}


# =====================================================================
# BUCLE PRINCIPAL DEL MOTOR TUI
# =====================================================================
def run_tui(stdscr):
    """Sólo dibuja y procesa entrada; las consultas y acciones usan trabajadores."""
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.timeout(100)
    stdscr.keypad(True)
    try:
        curses.set_escdelay(25)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(150)
    except (AttributeError, curses.error):
        pass
    init_colors()
    theme.background(stdscr)
    state = TUIState()
    state.screen = stdscr
    state.tasks = PanelTasks(make_poller(state))
    state.tasks.start()
    try:
        while state.running:
            drain_tasks(state)
            if not state.running:
                break
            max_y, max_x = stdscr.getmaxyx()
            state.viewport_width = max_x
            state.mouse_regions = []
            state.modal_regions = []
            stdscr.erase()
            if max_y < 24 or max_x < 76:
                safe_addstr(stdscr, 1, 2, 'thinkISP', curses.A_BOLD)
                safe_addstr(stdscr, 3, 2, 'Amplía la terminal a 76 columnas y 24 filas.', theme.color(3))
                safe_addstr(stdscr, 5, 2, f'Tamaño actual: {max_x} × {max_y} · Q Salir', curses.A_DIM)
                render_footer(stdscr, state, max_y, max_x)
            else:
                render_page(stdscr, state, max_y, max_x)
                renderers = {
                    MODAL_QR: render_qr_modal, MODAL_TIMER: render_timer_modal,
                    MODAL_SPEEDTEST: render_speedtest_modal, MODAL_DNS: render_dns_modal,
                    MODAL_LIMIT: render_limit_modal, MODAL_KICK: render_kick_modal,
                    MODAL_HELP: render_help_modal, MODAL_BLOCKED: render_blocked_modal,
                    MODAL_COMMANDS: render_commands_modal,
                }
                if state.modal in renderers:
                    renderers[state.modal](stdscr, state, max_y, max_x)
            stdscr.noutrefresh()
            curses.doupdate()
            try:
                ch = stdscr.getch()
            except KeyboardInterrupt:
                execute_button_action('quit', state)
                continue
            if ch == -1:
                continue
            if ch == curses.KEY_RESIZE:
                continue
            if max_y < 24 or max_x < 76:
                if ch in (ord('q'), ord('Q'), 27):
                    execute_button_action('quit', state)
                continue
            try:
                if ch == curses.KEY_MOUSE:
                    _, mx, my, _, flags = curses.getmouse()
                    if flags & (curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED):
                        handle_mouse(my, mx, state, activate=bool(flags & curses.BUTTON1_DOUBLE_CLICKED))
                    elif flags & curses.BUTTON4_PRESSED:
                        handle_keypress(curses.KEY_UP, state, max_x)
                    elif flags & getattr(curses, "BUTTON5_PRESSED", 0):
                        handle_keypress(curses.KEY_DOWN, state, max_x)
                else:
                    handle_keypress(ch, state, max_x)
            except (ThinkISPError, OSError, curses.error) as exc:
                state.set_status(f'Error: {exc}', 2)
    finally:
        state.tasks.close()
        close_modal(state)


# =====================================================================
# FUNCIÓN PRINCIPAL DE ENTRADA
# =====================================================================
def launch_tui():
    """Punto de entrada exportado para inicializar el TUI dentro de curses.wrapper."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ThinkISPError("El panel necesita una terminal interactiva. Usa 'thinkisp status' o 'thinkisp doctor --json' sin terminal.")
    if not os.environ.get('TERM') or os.environ['TERM'] == 'dumb':
        raise ThinkISPError("La terminal no anuncia soporte curses (TERM). Usa los comandos CLI o una terminal compatible.")
    if locale.nl_langinfo(locale.CODESET).lower().replace('-', '') != 'utf8':
        raise ThinkISPError("El panel necesita una configuración regional UTF-8. Usa un locale UTF-8 instalado en el sistema o los comandos CLI.")
    def dashboard(screen):
        ensure_setup(screen=screen)
        run_tui(screen)
    try:
        curses.wrapper(dashboard)
    except curses.error as exc:
        raise ThinkISPError(f"No se pudo iniciar el panel con esta terminal: {exc}. Usa 'thinkisp status'.") from exc
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    launch_tui()
