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
from types import SimpleNamespace
import time

# Configuración y Core de ThinkISP
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

# El encabezado se construye con los datos del equipo en render_header().


# =====================================================================
# CLASE DE ESTADO GLOBAL DE LA INTERFAZ (TUIState)
# =====================================================================
class TUIState:
    def __init__(self):
        self.running = True
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
        self.interface = WLAN_IFACE or "No detectada"
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
        self.status_msg_color = curses.color_pair(4) if curses.has_colors() else 0

        # Regiones clicables del ratón (y, x, w, h, tipo, id)
        self.mouse_regions = []
        self.modal_regions = []
        self.viewport_width = 80

    def set_status(self, msg, color_pair_id=4):
        """Establece un mensaje de estado temporal en el footer."""
        self.status_msg = msg
        self.status_msg_time = time.time()
        self.status_msg_color = curses.color_pair(color_pair_id) if curses.has_colors() else 0


# =====================================================================
# UTILIDADES DE PINTADO CURSES SEGURO
# =====================================================================
def focus_style():
    colored = curses.has_colors() and getattr(curses, 'COLOR_PAIRS', 0) > 15
    return (curses.color_pair(8) if colored else curses.A_REVERSE) | curses.A_BOLD


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
        available = max_x - x - (1 if y == max_y - 1 else 0)
        if available > 0:
            win.addstr(y, x, fit_text(text, available), attr)
    except (curses.error, UnicodeError):
        pass


def draw_box(win, y, x, h, w, title="", attr=0, title_attr=0):
    """Dibuja un marco discreto sin depender de un tipo de terminal concreto."""
    if h < 2 or w < 2:
        return
    safe_addstr(win, y, x, "┌" + "─" * (w - 2) + "┐", attr)
    for row in range(y + 1, y + h - 1):
        safe_addstr(win, row, x, "│", attr)
        safe_addstr(win, row, x + w - 1, "│", attr)
    safe_addstr(win, y + h - 1, x, "└" + "─" * (w - 2) + "┘", attr)

    if title:
        t_clean = f" {title} "
        if len(t_clean) < w - 4:
            safe_addstr(win, y, x + 2, t_clean, title_attr if title_attr else attr | curses.A_BOLD)


# =====================================================================
# INICIALIZACIÓN DE PALETA DE COLORES
# =====================================================================
def init_colors():
    """Inicializa pares de colores optimizados para terminales oscuros modernos."""
    if not curses.has_colors():
        return

    curses.start_color()
    bg = -1
    try:
        curses.use_default_colors()
    except curses.error:
        bg = curses.COLOR_BLACK

    # Un acento cian; verde, ámbar y rojo comunican resultados y estados.
    try:
        curses.init_pair(1, curses.COLOR_GREEN, bg)
        curses.init_pair(2, curses.COLOR_RED, bg)
        curses.init_pair(3, curses.COLOR_YELLOW, bg)
        curses.init_pair(4, curses.COLOR_CYAN, bg)
        curses.init_pair(5, curses.COLOR_CYAN, bg)
        curses.init_pair(6, curses.COLOR_CYAN, bg)
        curses.init_pair(7, curses.COLOR_WHITE, bg)
        curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(10, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(11, curses.COLOR_BLACK, curses.COLOR_RED)
        curses.init_pair(12, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        curses.init_pair(13, curses.COLOR_WHITE, bg)
        curses.init_pair(14, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(15, curses.COLOR_BLACK, curses.COLOR_WHITE)
    except curses.error:
        pass


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
    rx_bytes, tx_bytes = get_iface_bytes(WLAN_IFACE)

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

    # 5. Lista de clientes conectados (refrescar cada ciclo)
    if state.hotspot_active:
        state.clients = get_connected_clients(check=True)
        blocked = set(get_blocked_clients())
        for client in state.clients:
            client['blocked'] = client.get('mac') in blocked
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
    selected = None
    if state.clients and state.client_index < len(state.clients):
        selected = state.clients[state.client_index].get('mac')
    for key, value in snapshot.items():
        setattr(state, key, value)
    state.client_index = next((i for i, client in enumerate(state.clients) if client.get('mac') == selected),
                              min(state.client_index, max(0, len(state.clients) - 1)))
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
    safe_addstr(win, 0, 2, "thinkISP", curses.color_pair(4) | curses.A_BOLD)
    safe_addstr(win, 0, 13, fit_text(state.hostname, max_x - 40), curses.A_BOLD)
    if state.poll_error:
        label, color = 'DATOS SIN ACTUALIZAR', 3
    elif state.last_refresh is None:
        label, color = 'CONSULTANDO', 3
    else:
        age = time.monotonic() - state.last_refresh
        label, color = (f'SIN ACTUALIZAR {int(age)} s', 3) if age > 8 else ('EN VIVO', 4)
    safe_addstr(win, 0, max_x - len(label) - 2, label, curses.color_pair(color))
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
        status, color = 'Punto de acceso activo', 1
    else:
        status, color = 'Punto de acceso apagado', 7
    safe_addstr(win, start_y + 1, 3, fit_text(status, left_w), curses.color_pair(color) | curses.A_BOLD)
    safe_addstr(win, start_y + 1, right_x, fit_text(f"Red: {state.ssid}", right_w), curses.A_BOLD)
    upstream = state.ethernet
    link = f"{upstream.get('interface') or 'Sin datos'} · {upstream.get('speed') or 'Sin datos'}" if upstream.get('connected') else 'Sin conexión detectada'
    safe_addstr(win, start_y + 2, 3, fit_text(f"Puerta de enlace: {state.ip_gateway}", left_w))
    safe_addstr(win, start_y + 2, right_x, fit_text(f"Salida: {link}", right_w), curses.color_pair(7 if upstream.get('connected') else 3))
    safe_addstr(win, start_y + 3, 3, fit_text(f"DNS: {state.current_dns}", inner), curses.color_pair(7))
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
        safe_addstr(win, start_y + 2, x, fit_text(value, width), curses.A_BOLD | curses.color_pair(4 if i else 7))
    return start_y + 4


def button_columns(max_x):
    return max(3, min(5, (max_x - 4) // 18))


def render_buttons(win, state, start_y, max_x):
    state.mouse_regions = [r for r in state.mouse_regions if r.get('type') != 'button']
    cols = button_columns(max_x)
    btn_w = (max_x - 4) // cols
    rows = (len(BUTTON_DEFS) + cols - 1) // cols
    safe_addstr(win, start_y, 2, "ACCIONES", curses.A_DIM)
    if state.focus == FOCUS_BUTTONS:
        hint = BUTTON_DEFS[state.btn_index]['hint']
        safe_addstr(win, start_y, 13, fit_text(hint, max_x - 16), curses.A_DIM)
    for idx, definition in enumerate(BUTTON_DEFS):
        label = definition['label']
        if definition['id'] == 'toggle':
            label = 'Apagar red' if state.hotspot_active else 'Encender red'
        text = fit_text(f" [{definition['hotkey']}] {label}", btn_w - 1, pad=True)
        focused = state.focus == FOCUS_BUTTONS and state.btn_index == idx
        attr = focus_style() if focused else curses.color_pair(7)
        if state.busy_label and definition['id'] not in ('help', 'quit'):
            attr = curses.A_DIM
        x, y = 2 + idx % cols * btn_w, start_y + 1 + idx // cols
        safe_addstr(win, y, x, text, attr)
        state.mouse_regions.append({'type': 'button', 'rect': (y, x, btn_w - 1, 1), 'index': idx, 'id': definition['id']})
    return start_y + rows + 1


def client_columns(max_x):
    columns = [('hostname', 'DISPOSITIVO', 19), ('ip', 'DIRECCIÓN IP', 15), ('signal', 'SEÑAL', 9)]
    if max_x >= 96:
        columns.append(('mac', 'DIRECCIÓN MAC', 17))
    if max_x >= 116:
        columns.append(('rate', 'ENLACE TX/RX', 15))
    columns.append(('conn_time', 'CONECTADO', 12))
    return columns


def render_clients_table(win, state, start_y, max_y, max_x):
    state.mouse_regions = [r for r in state.mouse_regions if r.get('type') != 'client']
    height = max_y - start_y - 2
    if height < 5:
        return
    focused = state.focus == FOCUS_CLIENTS
    title = f"DISPOSITIVOS · {len(state.clients)}" if not state.poll_error else "DISPOSITIVOS · ÚLTIMA LECTURA"
    draw_box(win, start_y, 1, height, max_x - 2, title, curses.color_pair(4) if focused else curses.A_DIM)
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
            'rate': '/'.join(f'{client[key]:.0f}' if client.get(key) is not None else '—' for key in ('tx_mbps', 'rx_mbps')) + ' Mbps',
            'conn_time': format_duration(client['conn_time']) if client.get('conn_time') is not None else '—',
        }
        y = start_y + 2 + index - offset
        selected = focused and state.client_index == index
        if selected:
            safe_addstr(win, y, 2, ' ' * (max_x - 4), curses.color_pair(8))
        x = 3
        for key, _, width in columns:
            attr = focus_style() if selected else curses.color_pair(7)
            safe_addstr(win, y, x, fit_text(values[key], width, pad=True), attr)
            x += width + 1
        state.mouse_regions.append({'type': 'client', 'rect': (y, 2, max_x - 4, 1), 'index': index})
    if len(state.clients) > visible:
        counter = f' {offset + 1}–{min(offset + visible, len(state.clients))} de {len(state.clients)} '
        safe_addstr(win, start_y + height - 1, max_x - len(counter) - 4, counter, curses.A_DIM)


def render_footer(win, state, max_y, max_x):
    if state.busy_label:
        spinner = '|/−\\'[int(time.monotonic() * 5) % 4]
        message, attr = f"{spinner} {state.busy_label}…" + (' Se cerrará al terminar.' if state.exit_requested else ''), curses.color_pair(4) | curses.A_BOLD
    elif time.time() - state.status_msg_time < 8:
        message, attr = state.status_msg, state.status_msg_color
    elif state.poll_error:
        message, attr = f'Actualización fallida: {state.poll_error}', curses.color_pair(3)
    elif state.focus == FOCUS_CLIENTS:
        message, attr = '[Enter] Ver dispositivo y controlar su acceso', curses.color_pair(4)
    else:
        message, attr = BUTTON_DEFS[state.btn_index]['hint'], curses.A_DIM
    safe_addstr(win, max_y - 2, 2, fit_text(message, max_x - 4), attr)
    help_text = 'Tab Panel   Flechas Mover   Enter Elegir   ? Ayuda   Q Salir'
    safe_addstr(win, max_y - 1, 2, fit_text(help_text, max_x - 4), curses.A_DIM)


# =====================================================================
# MODALES EMERGENTES (POP-UPS)
# =====================================================================

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
        safe_addstr(win, y + 3, x + 4, fit_text(data['error'], width - 8), curses.color_pair(2))
        return
    row = y + 2
    for line in visible_lines:
        qr_x = x + (width - qr_width) // 2
        safe_addstr(win, row, qr_x, line.ljust(qr_width), curses.color_pair(15))
        row += 1
    if not visible_lines:
        note = data.get('note') or 'Amplía la terminal para ver el QR completo.'
        safe_addstr(win, row, x + 4, fit_text(note, width - 8), curses.color_pair(3))
        row += 2
    safe_addstr(win, row, x + 4, fit_text(f"Red: {data.get('ssid', state.ssid)}", width - 8), curses.A_BOLD)
    safe_addstr(win, row + 1, x + 4, fit_text(f"Contraseña: {data.get('psk', '')}", width - 8), curses.A_BOLD)
    safe_addstr(win, row + 2, x + 4, fit_text(f"Seguridad: {data.get('security', state.security)}", width - 8), curses.A_DIM)


def modal_frame(win, max_y, max_x, height, width, title):
    y, x = max(1, (max_y - height) // 2), max(2, (max_x - width) // 2)
    for row in range(height):
        safe_addstr(win, y + row, x, ' ' * width, curses.color_pair(13))
    draw_box(win, y, x, height, width, title, curses.color_pair(4))
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
    safe_addstr(win, y, bx, label, curses.color_pair(7) | curses.A_DIM)
    state.modal_regions.append({'rect': (y, bx, len(label), 1), 'key': 27})


def render_timer_modal(win, state, max_y, max_x):
    width, height = 64, 16
    y, x = modal_frame(win, max_y, max_x, height, width, 'PROGRAMAR APAGADO')
    timer = state.timer_info or {}
    current = timer.get('remaining_str') or (f"{timer['idle_mins']} min sin clientes" if timer.get('idle_mins') else 'Sin programación')
    modal_line(win, y + 2, x, width, f'Actual: {current}', curses.A_BOLD)
    modal_line(win, y + 4, x, width, 'Selecciona una opción con las flechas izquierda / derecha.', curses.A_DIM)
    labels = ['15 min', '30 min', '45 min', '1 hora', '2 horas', 'Inactividad 10 min', 'Desprogramar']
    selected = state.modal_data.get('preset_idx', 1)
    for i, label in enumerate(labels):
        row, col = divmod(i, 4)
        px = x + 3 + (col * 14 if row == 0 else col * 19)
        attr = focus_style() if i == selected else curses.color_pair(7)
        span = 14 if row == 0 else 18
        safe_addstr(win, y + 6 + row * 2, px, fit_text(label, span), attr)
        state.modal_regions.append({'rect': (y + 6 + row * 2, px, span, 1), 'field': 'preset_idx', 'value': i})
    modal_line(win, y + 10, x, width, 'O escribe: 45m, 1h20m o idle 10 (minutos sin clientes).', curses.A_DIM)
    text = state.modal_data.get('custom_text', '')
    modal_line(win, y + 11, x, width, f'Tiempo: {text}_', curses.color_pair(4) | curses.A_BOLD)
    modal_controls(win, state, y + 14, x, width, 'Aplicar')


def render_speedtest_modal(win, state, max_y, max_x):
    width, height = 64, 16
    y, x = modal_frame(win, max_y, max_x, height, width, 'PRUEBA DE CONEXIÓN')
    data = state.modal_data
    running = data.get('running', False)
    error = data.get('error')
    summary = 'Midiendo conexión y transferencia…' if running else ('La prueba terminó con errores.' if error else 'Prueba completada.')
    modal_line(win, y + 2, x, width, summary, curses.color_pair(3 if running or error else 1) | curses.A_BOLD)
    modal_line(win, y + 4, x, width, 'Conexión HTTP', curses.A_DIM)
    modal_line(win, y + 5, x, width, f"{data['ping_ms']:.1f} ms" if data.get('ping_ms') is not None else '—', curses.A_BOLD)
    for row, label, key in [(7, 'Descarga', 'down_mbps'), (9, 'Subida', 'up_mbps')]:
        value = f"{data[key]:.1f} Mbps" if data.get(key) is not None else '—'
        modal_line(win, y + row, x, width, f'{label:<18}{value}', curses.A_BOLD)
    modal_line(win, y + 11, x, width, 'Cloudflare · descarga 25 MB / subida 5 MB', curses.A_DIM)
    if error:
        modal_line(win, y + 12, x, width, str(error), curses.color_pair(2))
    modal_controls(win, state, y + 14, x, width)
    if running:
        modal_line(win, y + 12, x, width, 'Puedes cerrar el diálogo mientras se mide.', curses.A_DIM)


def render_dns_modal(win, state, max_y, max_x):
    width, height = 70, 16
    y, x = modal_frame(win, max_y, max_x, height, width, 'SERVIDORES DNS')
    modal_line(win, y + 2, x, width, 'PROVEEDOR                  SERVIDOR          RESPUESTA', curses.A_DIM)
    data = state.modal_data
    if data.get('running'):
        modal_line(win, y + 5, x, width, 'Comparando respuesta de los proveedores…', curses.color_pair(4))
    elif data.get('error'):
        modal_line(win, y + 5, x, width, str(data['error']), curses.color_pair(2))
    else:
        for i, item in enumerate(data.get('results', [])):
            if i >= height - 7:
                break
            delay = '—' if item['key'] == 'restore' else (f"{item['time']} ms" if item.get('time') is not None else 'Sin respuesta')
            row = f"{fit_text(item['name'], 26, pad=True)} {fit_text(item['ip'], 17, pad=True)} {delay}"
            attr = focus_style() if i == data.get('selected_idx', 0) else curses.color_pair(7)
            modal_line(win, y + 4 + i, x, width, row, attr)
            state.modal_regions.append({'rect': (y + 4 + i, x + 3, width - 6, 1), 'field': 'selected_idx', 'value': i})
    modal_line(win, y + 12, x, width, fit_text(f'Actual: {state.current_dns}', width - 6), curses.A_DIM)
    modal_controls(win, state, y + 14, x, width, None if data.get('running') or data.get('error') else 'Aplicar')


def render_limit_modal(win, state, max_y, max_x):
    width, height = 60, 14
    y, x = modal_frame(win, max_y, max_x, height, width, 'LÍMITE DE ANCHO DE BANDA')
    modal_line(win, y + 2, x, width, f"Actual: {state.rate_limit or 'Sin límite'}", curses.A_BOLD)
    modal_line(win, y + 4, x, width, 'Selecciona con las flechas izquierda / derecha.', curses.A_DIM)
    labels = ['5 Mbps', '15 Mbps', '30 Mbps', '50 Mbps', '100 Mbps', 'Sin límite']
    for i, label in enumerate(labels):
        row, col = divmod(i, 3)
        attr = focus_style() if i == state.modal_data.get('preset_idx', 2) else curses.color_pair(7)
        px = x + 4 + col * 17
        safe_addstr(win, y + 6 + row * 2, px, label, attr)
        state.modal_regions.append({'rect': (y + 6 + row * 2, px, len(label), 1), 'field': 'preset_idx', 'value': i})
    modal_line(win, y + 10, x, width, 'Aplicar un límite sustituye las colas CAKE.', curses.A_DIM)
    modal_controls(win, state, y + 12, x, width, 'Aplicar')


def render_kick_modal(win, state, max_y, max_x):
    width, height = 66, 16
    y, x = modal_frame(win, max_y, max_x, height, width, 'DISPOSITIVO / CONTROL DE ACCESO')
    client = state.modal_data.get('target_client', {})
    for row, label, key in [(2, 'Nombre', 'hostname'), (4, 'Dirección IP', 'ip'), (5, 'Dirección MAC', 'mac')]:
        modal_line(win, y + row, x, width, f"{label:<16}{client.get(key) or 'Sin datos'}", curses.A_BOLD if row == 2 else 0)
    signal = client.get('signal')
    modal_line(win, y + 6, x, width, f"Señal           {str(signal) + ' dBm' if signal is not None else 'Sin datos'}")
    blocked = client.get('blocked', False)
    modal_line(win, y + 8, x, width, 'Bloqueo configurado' if blocked else 'Sin bloqueo configurado', curses.color_pair(3 if blocked else 7))
    modal_line(win, y + 10, x, width, 'El bloqueo impide su acceso al equipo e Internet.', curses.A_DIM)
    modal_line(win, y + 11, x, width, 'Puede seguir asociado al Wi-Fi. La red no se reinicia.', curses.A_DIM)
    action = 'Permitir acceso' if blocked else 'Bloquear acceso'
    modal_controls(win, state, y + 14, x, width, action)


def render_help_modal(win, state, max_y, max_x):
    width, height = min(74, max_x - 4), 22
    y, x = modal_frame(win, max_y, max_x, height, width, 'CONTROLES DEL PANEL')
    lines = [
        ('Tab / Shift+Tab', 'Cambiar entre acciones y dispositivos'),
        ('Flechas', 'Mover la selección'),
        ('Enter', 'Abrir acción o ver dispositivo'),
        ('RePág / AvPág', 'Recorrer dispositivos por páginas'),
        ('Inicio / Fin', 'Primer o último dispositivo'),
        ('O', 'Encender o apagar el punto de acceso'),
        ('C', 'Configurar red y parámetros de la tarjeta'),
        ('R', 'Ver credenciales y código QR'),
        ('T', 'Programar apagado'),
        ('G / L', 'Colas CAKE / límite de ancho de banda'),
        ('D / A', 'Servidores DNS / filtro AdGuard DNS'),
        ('S', 'Ocultar o mostrar la red'),
        ('P / B', 'Prueba de conexión / optimizar Wi-Fi'),
        ('K', 'Controlar acceso del dispositivo seleccionado'),
        ('Esc', 'Cerrar diálogo sin aplicar cambios'),
        ('Q', 'Salir del panel; conserva el estado de la red'),
        ('Ratón', 'Clic para elegir una acción o dispositivo'),
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
    elif btn_id == 'config':
        if state.tasks:
            state.tasks.pause(True)
        curses.def_prog_mode()
        curses.endwin()
        try:
            configure_hotspot()
            state.set_status('Configuración guardada.', 1)
        except (KeyboardInterrupt, EOFError):
            state.set_status('Configuración cancelada.', 3)
        finally:
            curses.reset_prog_mode()
            curses.doupdate()
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
def handle_mouse(my, mx, state):
    """Procesa eventos de clic del ratón sobre botones o filas de clientes."""
    if state.modal != MODAL_NONE:
        for region in state.modal_regions:
            y, x, width, height = region['rect']
            if y <= my < y + height and x <= mx < x + width:
                if 'key' in region:
                    handle_keypress(region['key'], state, state.viewport_width)
                else:
                    state.modal_data[region['field']] = region['value']
                break
        return

    for reg in state.mouse_regions:
        y, x, w, h = reg["rect"]
        if y <= my < y + h and x <= mx < x + w:
            rtype = reg.get("type")
            if rtype == "button":
                state.focus = FOCUS_BUTTONS
                state.btn_index = reg["index"]
                execute_button_action(reg["id"], state)
                return
            elif rtype == "client":
                state.focus = FOCUS_CLIENTS
                state.client_index = reg["index"]
                return


# =====================================================================
# MANEJADOR DE EVENTOS DE TECLADO
# =====================================================================
def handle_keypress(ch, state, max_x):
    """Procesa la entrada del teclado considerando foco y modales."""
    # 1. Manejo cuando hay un modal abierto
    if state.modal != MODAL_NONE:
        if ch in (27, ord('q'), ord('Q')):  # Esc o Q
            close_modal(state)
            return

        if state.modal in (MODAL_QR, MODAL_HELP):
            if ch in (10, 13, 32):  # Enter o Espacio
                close_modal(state)
            return

        elif state.modal == MODAL_TIMER:
            presets = ["15m", "30m", "45m", "1h", "2h", "idle 10", "cancel"]
            cur_p = state.modal_data.get("preset_idx", 0)

            if ch == curses.KEY_LEFT:
                state.modal_data["preset_idx"] = max(0, cur_p - 1)
            elif ch == curses.KEY_RIGHT:
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
            presets = ["5", "15", "30", "50", "100", "reset"]
            cur_p = state.modal_data.get("preset_idx", 0)
            if ch == curses.KEY_LEFT:
                state.modal_data["preset_idx"] = max(0, cur_p - 1)
            elif ch == curses.KEY_RIGHT:
                state.modal_data["preset_idx"] = min(len(presets) - 1, cur_p + 1)
            elif ch in (10, 13):  # Enter
                value = presets[cur_p]
                operation = clear_rate_limit if value == 'reset' else lambda: set_rate_limit(value)
                if submit_action(state, 'Configurando límite', lambda: checked_result(operation())):
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

    # 2. Navegación y Atajos Globales (sin modal activo)
    # Atajos de una sola letra
    key_map = {
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
    if ch in (9, curses.KEY_BTAB):  # Tab o Shift-Tab
        state.focus = FOCUS_CLIENTS if state.focus == FOCUS_BUTTONS else FOCUS_BUTTONS
        return

    # Flechas de navegación según panel enfocado
    if state.focus == FOCUS_BUTTONS:
        cols = button_columns(max_x)
        total_btns = len(BUTTON_DEFS)

        if ch == curses.KEY_LEFT:
            state.btn_index = (state.btn_index - 1) % total_btns
        elif ch == curses.KEY_RIGHT:
            state.btn_index = (state.btn_index + 1) % total_btns
        elif ch == curses.KEY_UP:
            if state.btn_index >= cols:
                state.btn_index -= cols
        elif ch == curses.KEY_DOWN:
            if state.btn_index + cols < total_btns:
                state.btn_index += cols
            else:
                # Saltar al panel de clientes
                if state.clients:
                    state.focus = FOCUS_CLIENTS
        elif ch in (10, 13, 32):  # Enter o Espacio
            execute_button_action(BUTTON_DEFS[state.btn_index]["id"], state)

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
    state = TUIState()
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
                safe_addstr(stdscr, 3, 2, 'Amplía la terminal a 76 columnas y 24 filas.', curses.color_pair(3))
                safe_addstr(stdscr, 5, 2, f'Tamaño actual: {max_x} × {max_y} · Q Salir', curses.A_DIM)
                render_footer(stdscr, state, max_y, max_x)
            else:
                y = render_header(stdscr, state, max_y, max_x)
                y = render_status_bar(stdscr, state, y, max_x)
                y = render_metrics_bar(stdscr, state, y, max_x)
                y = render_buttons(stdscr, state, y, max_x)
                render_clients_table(stdscr, state, y, max_y, max_x)
                render_footer(stdscr, state, max_y, max_x)
                renderers = {
                    MODAL_QR: render_qr_modal, MODAL_TIMER: render_timer_modal,
                    MODAL_SPEEDTEST: render_speedtest_modal, MODAL_DNS: render_dns_modal,
                    MODAL_LIMIT: render_limit_modal, MODAL_KICK: render_kick_modal,
                    MODAL_HELP: render_help_modal,
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
                        handle_mouse(my, mx, state)
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
    ensure_setup()
    try:
        curses.wrapper(run_tui)
    except curses.error as exc:
        raise ThinkISPError(f"No se pudo iniciar el panel con esta terminal: {exc}. Usa 'thinkisp status'.") from exc
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    launch_tui()
