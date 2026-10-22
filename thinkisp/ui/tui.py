"""
ThinkISP - Motor de Interfaz Terminal Interactiva (TUI)
Construido con curses nativo (zero external dependencies).
Dashboard en tiempo real con soporte completo para teclado y ratón.
"""

import curses
from datetime import datetime
import json
import locale
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

# Configuración y Core de ThinkISP
from thinkisp.config import (
    DEFAULT_PSK,
    DEFAULT_SSID,
    DNS_PROVIDERS,
    ETH_IFACE,
    HOTSPOT_CONN,
    WLAN_IFACE,
)
from thinkisp.core.dns import (
    benchmark_dns,
    get_adblock_status,
    get_current_dns,
    run_speedtest,
    set_adblock,
    set_dns_provider,
)
from thinkisp.core.network import (
    format_bytes,
    format_duration,
    get_ethernet_status,
    get_hotspot_ip,
    get_iface_bytes,
    get_ip_forward_status,
    is_hotspot_active,
    load_state,
    notify,
    parse_duration_str,
    parse_speed_mbps,
    run_cmd,
    save_state,
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
    format_signal_bar,
    get_blocked_clients,
    get_connected_clients,
    get_stealth_mode,
    kick_client,
    set_stealth_mode,
    start_hotspot,
    stop_hotspot,
    unkick_client,
)

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
    {"id": "toggle", "label": "⏻ Hotspot", "hotkey": "O", "hint": "Encender/Apagar"},
    {"id": "boost", "label": "⚡ Boost", "hotkey": "B", "hint": "0 Latencia Turbo"},
    {"id": "gaming", "label": "🎮 CAKE", "hotkey": "G", "hint": "QoS Anti-Lag"},
    {"id": "adblock", "label": "🛡 AdBlock", "hotkey": "A", "hint": "Filtrado DNS"},
    {"id": "stealth", "label": "👻 Stealth", "hotkey": "S", "hint": "Ocultar SSID"},
    {"id": "timer", "label": "⏱ Timer", "hotkey": "T", "hint": "Apagado Prog."},
    {"id": "limit", "label": "⚡ Límite", "hotkey": "L", "hint": "Control Ancho Banda"},
    {"id": "dns", "label": "🌐 DNS", "hotkey": "D", "hint": "Benchmark DNS"},
    {"id": "speed", "label": "🚀 Speed", "hotkey": "P", "hint": "Test Velocidad"},
    {"id": "qr", "label": "📱 QR", "hotkey": "R", "hint": "Conectar Celular"},
    {"id": "help", "label": "❓ Ayuda", "hotkey": "?", "hint": "Atajos de Teclado"},
    {"id": "quit", "label": "✕ Salir", "hotkey": "Q", "hint": "Cerrar Dashboard"},
]

# Títulos ASCII Art adaptables
ASCII_BANNER_FULL = [
    r"  ████████╗██╗  ██╗██╗███╗   ██╗██╗  ██╗██╗███████╗██████╗    HOTSPOT MANAGER",
    r"  ╚══██╔══╝██║  ██║██║████╗  ██║██║ ██╔╝██║██╔════╝██╔══██╗   SPUTNIK v1.0",
    r"     ██║   ███████║██║██╔██╗ ██║█████═╝ ██║███████╗██████╔╝   ───────────────",
    r"     ██║   ██╔══██║██║██║╚██╗██║██╔═██╗ ██║╚════██║██╔═══╝    5GHz TURBO CORE",
    r"     ██║   ██║  ██║██║██║ ╚████║██║ ╚██╗██║███████║██║        ETHERNET ROUTER",
    r"     ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝╚══════╝╚═╝        BY THINKPAD AC8265",
]

ASCII_BANNER_MED = [
    r"  ╭────────────────────────────────────────────────────────────────────────────╮",
    r"  │  ⚡ SPUTNIK / THINKISP - GESTOR DE ZONA WI-FI DE ALTO RENDIMIENTO (5 GHz)   │",
    r"  ╰────────────────────────────────────────────────────────────────────────────╯",
]


# =====================================================================
# CLASE DE ESTADO GLOBAL DE LA INTERFAZ (TUIState)
# =====================================================================
class TUIState:
    def __init__(self):
        self.running = True
        self.focus = FOCUS_BUTTONS
        self.btn_index = 0
        self.client_index = 0
        self.modal = MODAL_NONE
        self.modal_data = {}

        # Estado del sistema y hardware
        self.hotspot_active = False
        self.ssid = DEFAULT_SSID
        self.psk = DEFAULT_PSK
        self.band = "5 GHz (Ch 149 / 80 MHz)"
        self.ip_gateway = "10.42.0.1/24"
        self.ethernet = {"connected": False, "speed": "Desconocida", "interface": ETH_IFACE}
        self.ip_forward = False

        # Modos y características
        self.gaming_mode = False
        self.adblock_mode = False
        self.stealth_mode = False
        self.current_dns = "Auto / DHCP"
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
        self._last_poll_time = time.time()
        self._last_rx_bytes = 0
        self._last_tx_bytes = 0
        self._poll_counter = 0

        # Barra de notificaciones de estado inferior
        self.status_msg = "ThinkISP listo. Presiona [?] para ayuda o [Tab] para navegar."
        self.status_msg_time = time.time()
        self.status_msg_color = curses.color_pair(4) if curses.has_colors() else 0

        # Regiones clicables del ratón (y, x, w, h, tipo, id)
        self.mouse_regions = []

    def set_status(self, msg, color_pair_id=4):
        """Establece un mensaje de estado temporal en el footer."""
        self.status_msg = msg
        self.status_msg_time = time.time()
        self.status_msg_color = curses.color_pair(color_pair_id) if curses.has_colors() else 0


# =====================================================================
# UTILIDADES DE PINTADO CURSES SEGURO
# =====================================================================
def safe_addstr(win, y, x, text, attr=0):
    """Escribe texto en la ventana de forma segura evitando desbordamientos de pantalla."""
    try:
        max_y, max_x = win.getmaxyx()
        if y < 0 or y >= max_y or x >= max_x:
            return
        if x < 0:
            text = text[-x:]
            x = 0
        avail = max_x - x
        if avail <= 0:
            return
        if y == max_y - 1 and len(text) >= avail:
            text = text[: avail - 1]
        else:
            text = text[:avail]
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def draw_box(win, y, x, h, w, title="", attr=0, title_attr=0):
    """Dibuja una caja con bordes Unicode redondeados."""
    if h < 2 or w < 2:
        return
    # Bordes redondeados
    safe_addstr(win, y, x, "╭" + "─" * (w - 2) + "╮", attr)
    for row in range(y + 1, y + h - 1):
        safe_addstr(win, row, x, "│", attr)
        safe_addstr(win, row, x + w - 1, "│", attr)
    safe_addstr(win, y + h - 1, x, "╰" + "─" * (w - 2) + "╯", attr)

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

    # Definición de pares de color:
    # 1: Verde / Éxito
    # 2: Rojo / Peligro / Inactivo
    # 3: Amarillo / Alerta / Atajos
    # 4: Cian / Primario / Headers
    # 5: Magenta / Especial / Stealth
    # 6: Azul / Upstream
    # 7: Blanco sobre Fondo Predeterminado
    # 8: Negro sobre Cian (Botón o elemento enfocado)
    # 9: Negro sobre Blanco (Fila de cliente seleccionada)
    # 10: Negro sobre Verde (Badge activo)
    # 11: Negro sobre Rojo (Badge inactivo)
    # 12: Negro sobre Amarillo (Badge warning)
    # 13: Cian sobre Negro (Marco de Modal)
    # 14: Blanco sobre Azul (Cabecera destacada)

    try:
        curses.init_pair(1, curses.COLOR_GREEN, bg)
        curses.init_pair(2, curses.COLOR_RED, bg)
        curses.init_pair(3, curses.COLOR_YELLOW, bg)
        curses.init_pair(4, curses.COLOR_CYAN, bg)
        curses.init_pair(5, curses.COLOR_MAGENTA, bg)
        curses.init_pair(6, curses.COLOR_BLUE, bg)
        curses.init_pair(7, curses.COLOR_WHITE, bg)
        curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(10, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(11, curses.COLOR_BLACK, curses.COLOR_RED)
        curses.init_pair(12, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        curses.init_pair(13, curses.COLOR_CYAN, curses.COLOR_BLACK)
        curses.init_pair(14, curses.COLOR_WHITE, curses.COLOR_BLUE)
    except curses.error:
        pass


# =====================================================================
# ACTUALIZACIÓN DE TELEMETRÍA Y ESTADO
# =====================================================================
def update_telemetry(state):
    """Consulta el estado del sistema, NetworkManager y calcula el flujo de tráfico."""
    now = time.time()
    dt = max(0.1, now - state._last_poll_time)

    # 1. Estado del Hotspot
    state.hotspot_active = is_hotspot_active()
    state.ethernet = get_ethernet_status()
    state.ip_gateway = get_hotspot_ip()
    state.ip_forward = get_ip_forward_status()

    # 2. Modos y configuraciones
    state.gaming_mode = get_gaming_mode()
    state.adblock_mode = get_adblock_status()
    state.stealth_mode = get_stealth_mode()
    state.current_dns = get_current_dns()
    state.rate_limit = get_rate_limit()
    state.timer_info = get_timer_status()

    # 3. Métricas de tráfico de red
    rx_bytes, tx_bytes = get_iface_bytes(WLAN_IFACE)

    if state._last_rx_bytes > 0 and state._last_tx_bytes > 0:
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

    state._last_poll_time = now
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
        state.clients = get_connected_clients()
    else:
        state.clients = []

    # Ajustar índice seleccionado si la lista cambió
    if state.clients:
        state.client_index = min(state.client_index, len(state.clients) - 1)
    else:
        state.client_index = 0


# =====================================================================
# RENDERIZADO DEL HEADER Y BANNER
# =====================================================================
def render_header(win, state, max_y, max_x):
    """Renderiza el banner estilizado o compacto según la altura del terminal."""
    curr_y = 0

    # Seleccionar banner según espacio vertical
    if max_y >= 30 and max_x >= 84:
        for line in ASCII_BANNER_FULL:
            safe_addstr(win, curr_y, 1, line, curses.color_pair(4) | curses.A_BOLD)
            curr_y += 1
    elif max_y >= 24:
        for line in ASCII_BANNER_MED:
            safe_addstr(win, curr_y, 1, line, curses.color_pair(4) | curses.A_BOLD)
            curr_y += 1
    else:
        title = "⚡ THINKISP / SPUTNIK - 5GHz HIGH-SPEED HOTSPOT"
        safe_addstr(win, curr_y, 2, title, curses.color_pair(4) | curses.A_BOLD)
        curr_y += 1

    return curr_y


# =====================================================================
# RENDERIZADO DEL PANEL DE ESTADO
# =====================================================================
def render_status_bar(win, state, start_y, max_x):
    """Renderiza la caja con el estado de la conexión, IP, Ethernet y modos."""
    box_h = 5
    box_w = max_x - 2
    draw_box(win, start_y, 1, box_h, box_w, "PANEL DE ESTADO DEL SISTEMA", curses.color_pair(4))

    # Fila 1: Estado ON/OFF, SSID, Frecuencia, IP Gateway
    y1 = start_y + 1
    safe_addstr(win, y1, 3, "Estado: ", curses.A_DIM)
    if state.hotspot_active:
        safe_addstr(win, y1, 11, "● ACTIVO (Transmitiendo)", curses.color_pair(1) | curses.A_BOLD)
    else:
        safe_addstr(win, y1, 11, "○ INACTIVO (Apagado)", curses.color_pair(2) | curses.A_BOLD)

    # SSID
    col_ssid = 38
    if max_x > 75:
        safe_addstr(win, y1, col_ssid, "SSID: ", curses.A_DIM)
        safe_addstr(win, y1, col_ssid + 6, f"{state.ssid}", curses.color_pair(7) | curses.A_BOLD)

    # Frecuencia / Banda
    col_band = 56
    if max_x > 95:
        safe_addstr(win, y1, col_band, "Banda: ", curses.A_DIM)
        safe_addstr(win, y1, col_band + 7, f"{state.band}", curses.color_pair(4))

    # Fila 2: IP Gateway, Ethernet, Firewall
    y2 = start_y + 2
    safe_addstr(win, y2, 3, "IP Gateway: ", curses.A_DIM)
    safe_addstr(win, y2, 15, f"{state.ip_gateway}", curses.color_pair(7) | curses.A_BOLD)

    # Ethernet
    col_eth = 38
    if max_x > 75:
        safe_addstr(win, y2, col_eth, "Ethernet: ", curses.A_DIM)
        if state.ethernet.get("connected"):
            eth_txt = f"✔ Gigabit ({state.ethernet.get('speed', '1000M')})"
            safe_addstr(win, y2, col_eth + 10, eth_txt, curses.color_pair(1) | curses.A_BOLD)
        else:
            safe_addstr(win, y2, col_eth + 10, "⚠ Desconectado", curses.color_pair(3) | curses.A_BOLD)

    # IP Forwarding
    col_fwd = 70
    if max_x > 95:
        safe_addstr(win, y2, col_fwd, "NAT Forward: ", curses.A_DIM)
        fwd_val = "✔ Activo" if state.ip_forward else "Auto (NM)"
        safe_addstr(win, y2, col_fwd + 13, fwd_val, curses.color_pair(1) if state.ip_forward else curses.color_pair(3))

    # Fila 3: Badges de Modos (Gaming CAKE, AdBlock, Stealth, Límite, DNS)
    y3 = start_y + 3
    safe_addstr(win, y3, 3, "Modos: ", curses.A_DIM)
    curr_x = 10

    # Badge Gaming
    g_txt = "[ 🎮 CAKE: ON ]" if state.gaming_mode else "[ CAKE: OFF ]"
    g_col = curses.color_pair(1) | curses.A_BOLD if state.gaming_mode else curses.color_pair(7) | curses.A_DIM
    safe_addstr(win, y3, curr_x, g_txt, g_col)
    curr_x += len(g_txt) + 1

    # Badge AdBlock
    a_txt = "[ 🛡 AdBlock: ON ]" if state.adblock_mode else "[ AdBlock: OFF ]"
    a_col = curses.color_pair(1) | curses.A_BOLD if state.adblock_mode else curses.color_pair(7) | curses.A_DIM
    safe_addstr(win, y3, curr_x, a_txt, a_col)
    curr_x += len(a_txt) + 1

    # Badge Stealth
    s_txt = "[ 👻 Stealth: ON ]" if state.stealth_mode else "[ Stealth: OFF ]"
    s_col = curses.color_pair(5) | curses.A_BOLD if state.stealth_mode else curses.color_pair(7) | curses.A_DIM
    if curr_x + len(s_txt) < box_w - 2:
        safe_addstr(win, y3, curr_x, s_txt, s_col)
        curr_x += len(s_txt) + 1

    # Badge DNS
    dns_clean = state.current_dns.replace(" DNS", "").replace(" Seguro", "")
    d_txt = f"[ 🌐 DNS: {dns_clean} ]"
    if curr_x + len(d_txt) < box_w - 2:
        safe_addstr(win, y3, curr_x, d_txt, curses.color_pair(4))
        curr_x += len(d_txt) + 1

    # Badge Límite
    if state.rate_limit and curr_x + 16 < box_w - 2:
        safe_addstr(win, y3, curr_x, f"[ ⚡ Límite: {state.rate_limit} ]", curses.color_pair(3) | curses.A_BOLD)

    return start_y + box_h


# =====================================================================
# RENDERIZADO DE MÉTRICAS EN VIVO
# =====================================================================
def render_metrics_bar(win, state, start_y, max_x):
    """Renderiza el panel con la duración, datos descargados/subidos y tasas instantáneas."""
    h = 3
    w = max_x - 2
    draw_box(win, start_y, 1, h, w, "MÉTRICAS Y CONSUMO EN TIEMPO REAL", curses.color_pair(6))

    y = start_y + 1

    # 1. Duración
    safe_addstr(win, y, 3, "⏱ Duración: ", curses.A_DIM)
    dur_str = format_duration(state.session_duration)
    safe_addstr(win, y, 15, dur_str, curses.color_pair(7) | curses.A_BOLD)

    # 2. Descarga total y tasa instantánea
    col_down = 32
    if max_x > 75:
        safe_addstr(win, y, col_down, "⬇ Descarga: ", curses.A_DIM)
        d_tot = format_bytes(state.session_downloaded)
        d_rate = f"({format_bytes(state.instant_down_bps)}/s)"
        safe_addstr(win, y, col_down + 12, f"{d_tot} ", curses.color_pair(1) | curses.A_BOLD)
        safe_addstr(win, y, col_down + 13 + len(d_tot), d_rate, curses.color_pair(1))

    # 3. Subida total y tasa instantánea
    col_up = 60
    if max_x > 95:
        safe_addstr(win, y, col_up, "⬆ Subida: ", curses.A_DIM)
        u_tot = format_bytes(state.session_uploaded)
        u_rate = f"({format_bytes(state.instant_up_bps)}/s)"
        safe_addstr(win, y, col_up + 10, f"{u_tot} ", curses.color_pair(4) | curses.A_BOLD)
        safe_addstr(win, y, col_up + 11 + len(u_tot), u_rate, curses.color_pair(4))

    # 4. Timer activo (si aplica)
    if state.timer_info:
        rem_str = state.timer_info.get("remaining_str", "")
        if rem_str:
            t_col = max_x - 28
            safe_addstr(win, y, t_col, f"⏰ Apagado en: {rem_str}", curses.color_pair(3) | curses.A_BOLD)

    return start_y + h


# =====================================================================
# RENDERIZADO DEL PANEL DE BOTONES INTERACTIVOS
# =====================================================================
def render_buttons(win, state, start_y, max_x):
    """Dibuja la cuadrícula interactiva de botones con soporte de navegación y ratón."""
    state.mouse_regions = [r for r in state.mouse_regions if r.get("type") != "button"]

    avail_w = max_x - 4
    # Calcular columnas según el ancho
    if avail_w >= 110:
        cols = 6
    elif avail_w >= 75:
        cols = 4
    else:
        cols = 3

    btn_w = avail_w // cols
    rows = (len(BUTTON_DEFS) + cols - 1) // cols
    panel_h = rows + 2

    draw_box(win, start_y, 1, panel_h, max_x - 2, "ACCIONES RÁPIDAS (Atajos de Teclado & Clic)", curses.color_pair(3))

    for idx, bdef in enumerate(BUTTON_DEFS):
        r = idx // cols
        c = idx % cols

        bx = 2 + c * btn_w
        by = start_y + 1 + r

        # Modificar etiqueta según estado dinámico
        label = bdef["label"]
        if bdef["id"] == "toggle":
            label = "⏻ Apagar" if state.hotspot_active else "⏻ Encender"
        elif bdef["id"] == "gaming" and state.gaming_mode:
            label = "🎮 CAKE (ON)"
        elif bdef["id"] == "adblock" and state.adblock_mode:
            label = "🛡 AdBlock (ON)"
        elif bdef["id"] == "stealth" and state.stealth_mode:
            label = "👻 Stealth (ON)"
        elif bdef["id"] == "limit" and state.rate_limit:
            label = f"⚡ Límite ({state.rate_limit})"
        elif bdef["id"] == "timer" and state.timer_info:
            rem = state.timer_info.get("remaining_str", "ON")
            label = f"⏱ Timer ({rem})"

        is_focused = (state.focus == FOCUS_BUTTONS and state.btn_index == idx)

        # Formatear botón
        hotkey_str = f"[{bdef['hotkey']}]"
        btn_text = f" {hotkey_str} {label} "
        # Truncar si excede el ancho de columna
        if len(btn_text) > btn_w - 1:
            btn_text = btn_text[: btn_w - 2] + " "

        # Registrar región para clic del ratón
        state.mouse_regions.append({
            "type": "button",
            "rect": (by, bx, len(btn_text), 1),
            "index": idx,
            "id": bdef["id"]
        })

        if is_focused:
            safe_addstr(win, by, bx, btn_text, curses.color_pair(8) | curses.A_BOLD)
        else:
            safe_addstr(win, by, bx, btn_text, curses.color_pair(7))
            # Resaltar la letra del atajo en amarillo
            hk_pos = btn_text.find(hotkey_str)
            if hk_pos != -1:
                safe_addstr(win, by, bx + hk_pos, hotkey_str, curses.color_pair(3) | curses.A_BOLD)

    return start_y + panel_h


# =====================================================================
# RENDERIZADO DE LA TABLA DE CLIENTES CONECTADOS
# =====================================================================
def render_clients_table(win, state, start_y, max_y, max_x):
    """Renderiza la tabla interactiva de clientes conectados con señal y velocidad."""
    state.mouse_regions = [r for r in state.mouse_regions if r.get("type") != "client"]

    table_h = max_y - start_y - 2
    if table_h < 4:
        return

    table_w = max_x - 2
    title = f"DISPOSITIVOS CONECTADOS ({len(state.clients)}) - Presiona [K] para expulsar"
    draw_box(win, start_y, 1, table_h, table_w, title, curses.color_pair(1) if state.hotspot_active else curses.color_pair(7))

    # Encabezados de columna
    header_y = start_y + 1
    safe_addstr(win, header_y, 2, " #  DIRECCIÓN IP     DIRECCIÓN MAC      DISPOSITIVO / HOSTNAME    SEÑAL WI-FI       VELOCIDAD       CONECTADO", curses.color_pair(4) | curses.A_BOLD)
    safe_addstr(win, header_y + 1, 2, "─" * (table_w - 4), curses.A_DIM)

    content_y = header_y + 2
    max_visible_rows = table_h - 4

    if not state.hotspot_active:
        safe_addstr(win, content_y, 4, "○ Zona Wi-Fi apagada. Presiona [O], [B] o [Espacio] para iniciar la transmisión.", curses.color_pair(3))
        return

    if not state.clients:
        safe_addstr(win, content_y, 4, "ℹ No hay dispositivos conectados. Esperando clientes en 5 GHz (SSID: sputnik)...", curses.color_pair(7) | curses.A_DIM)
        return

    for idx, c in enumerate(state.clients):
        if idx >= max_visible_rows:
            break

        row_y = content_y + idx
        is_selected = (state.focus == FOCUS_CLIENTS and state.client_index == idx)

        # Extraer campos
        ip_str = c.get("ip", "Desconocida").ljust(15)
        mac_str = c.get("mac", "00:00:00:00:00:00").ljust(17)
        host_str = c.get("hostname", "Dispositivo móvil")
        if len(host_str) > 23:
            host_str = host_str[:21] + ".."
        host_str = host_str.ljust(24)

        # Señal gráfica
        sig_val = c.get("signal")
        if sig_val is not None:
            if sig_val >= -50:
                sig_bar = f"[████] {sig_val} dBm"
                sig_col = curses.color_pair(1)
            elif sig_val >= -65:
                sig_bar = f"[███░] {sig_val} dBm"
                sig_col = curses.color_pair(4)
            elif sig_val >= -75:
                sig_bar = f"[██░░] {sig_val} dBm"
                sig_col = curses.color_pair(3)
            else:
                sig_bar = f"[█░░░] {sig_val} dBm"
                sig_col = curses.color_pair(2)
        else:
            sig_bar = "[????] N/D    "
            sig_col = curses.color_pair(7)

        sig_bar = sig_bar.ljust(16)

        # Velocidad TX/RX Mbps
        tx_m = c.get("tx_mbps")
        rx_m = c.get("rx_mbps")
        if tx_m or rx_m:
            speed_str = f"{tx_m or 0:.0f}/{rx_m or 0:.0f}M".ljust(14)
        else:
            speed_str = "N/D".ljust(14)

        # Tiempo conectado
        conn_t = c.get("conn_time")
        if conn_t is not None:
            conn_str = format_duration(conn_t)
        else:
            conn_str = "--"

        row_text = f" {idx+1:<2} {ip_str} {mac_str} {host_str} {sig_bar} {speed_str} {conn_str}"
        row_text = row_text.ljust(table_w - 4)

        # Registrar región para clic del ratón
        state.mouse_regions.append({
            "type": "client",
            "rect": (row_y, 2, table_w - 4, 1),
            "index": idx
        })

        if is_selected:
            safe_addstr(win, row_y, 2, row_text, curses.color_pair(9) | curses.A_BOLD)
        else:
            safe_addstr(win, row_y, 2, row_text, curses.color_pair(7))
            # Resaltar la señal con su propio color
            sig_pos = row_text.find(sig_bar)
            if sig_pos != -1:
                safe_addstr(win, row_y, 2 + sig_pos, sig_bar, sig_col)


# =====================================================================
# RENDERIZADO DEL FOOTER Y NOTIFICACIONES
# =====================================================================
def render_footer(win, state, max_y, max_x):
    """Dibuja la barra de estado y los atajos de teclado globales."""
    msg_y = max_y - 2
    help_y = max_y - 1

    # Línea 1: Mensaje de retroalimentación de la última acción
    safe_addstr(win, msg_y, 2, "• " + state.status_msg.ljust(max_x - 4), state.status_msg_color)

    # Línea 2: Atajos principales
    shortcuts = "[Tab] Cambiar Panel │ [↑/↓/←/→] Moverse │ [Enter] Activar │ [K] Expulsar │ [?] Ayuda │ [Q] Salir"
    safe_addstr(win, help_y, 2, shortcuts, curses.color_pair(7) | curses.A_DIM)


# =====================================================================
# MODALES EMERGENTES (POP-UPS)
# =====================================================================

def render_qr_modal(win, state, max_y, max_x):
    """Renderiza el modal con el código QR para conectar smartphones."""
    # Intentar generar QR con qrencode
    qr_data = f"WIFI:T:WPA;S:{state.ssid};P:{state.psk};;"
    qr_lines = []

    if shutil.which("qrencode"):
        try:
            res = subprocess.run(["qrencode", "-t", "UTF8", qr_data], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                qr_lines = [l for l in res.stdout.splitlines() if l.strip()]
        except Exception:
            pass

    modal_w = 48
    modal_h = len(qr_lines) + 8 if qr_lines else 14
    modal_h = min(modal_h, max_y - 2)
    modal_w = min(modal_w, max_x - 4)

    start_y = max(1, (max_y - modal_h) // 2)
    start_x = max(2, (max_x - modal_w) // 2)

    # Limpiar fondo del modal
    for r in range(modal_h):
        safe_addstr(win, start_y + r, start_x, " " * modal_w, curses.color_pair(13))

    draw_box(win, start_y, start_x, modal_h, modal_w, "CONEXIÓN RÁPIDA: CÓDIGO QR", curses.color_pair(4) | curses.A_BOLD)

    cur_y = start_y + 1
    if qr_lines:
        for ql in qr_lines:
            if cur_y >= start_y + modal_h - 6:
                break
            pad = max(0, (modal_w - len(ql)) // 2)
            safe_addstr(win, cur_y, start_x + pad, ql, curses.color_pair(7) | curses.A_BOLD)
            cur_y += 1
    else:
        safe_addstr(win, cur_y + 1, start_x + 4, "⚠ qrencode no disponible en el sistema.", curses.color_pair(3))
        cur_y += 3

    cur_y = max(cur_y, start_y + modal_h - 5)
    safe_addstr(win, cur_y, start_x + 4, f"SSID:        {state.ssid}", curses.color_pair(1) | curses.A_BOLD)
    safe_addstr(win, cur_y + 1, start_x + 4, f"Contraseña:  {state.psk}", curses.color_pair(3) | curses.A_BOLD)
    safe_addstr(win, cur_y + 2, start_x + 4, "Seguridad:   WPA2-PSK (5 GHz 80MHz)", curses.color_pair(7))

    safe_addstr(win, start_y + modal_h - 2, start_x + 8, "[ Presiona Esc o Enter para cerrar ]", curses.color_pair(4) | curses.A_BOLD)


def render_timer_modal(win, state, max_y, max_x):
    """Renderiza el modal de configuración de temporizador de apagado."""
    modal_w = 60
    modal_h = 16
    start_y = max(1, (max_y - modal_h) // 2)
    start_x = max(2, (max_x - modal_w) // 2)

    for r in range(modal_h):
        safe_addstr(win, start_y + r, start_x, " " * modal_w, curses.color_pair(13))

    draw_box(win, start_y, start_x, modal_h, modal_w, "TEMPORIZADOR DE APAGADO AUTOMÁTICO", curses.color_pair(3) | curses.A_BOLD)

    # Estado actual
    if state.timer_info:
        rem_str = state.timer_info.get("remaining_str", "Calculando...")
        exp_str = state.timer_info.get("end_time_str", "--")
        safe_addstr(win, start_y + 2, start_x + 4, f"● Activo: Se apagará en {rem_str} ({exp_str})", curses.color_pair(1) | curses.A_BOLD)
    else:
        safe_addstr(win, start_y + 2, start_x + 4, "○ No hay temporizador activo actualmente.", curses.color_pair(7) | curses.A_DIM)

    safe_addstr(win, start_y + 4, start_x + 4, "Selecciona un preset con [← / →] o ingresa tiempo:", curses.color_pair(4))

    presets = ["15m", "30m", "45m", "1h", "2h", "idle 10", "cancel"]
    preset_labels = ["[ 15 min ]", "[ 30 min ]", "[ 45 min ]", "[ 1 hora ]", "[ 2 horas ]", "[ Inactividad ]", "[ Cancelar ]"]

    p_idx = state.modal_data.get("preset_idx", 0)

    # Dibujar presets en 2 filas
    px = start_x + 4
    py = start_y + 6
    for idx, (p_code, p_lbl) in enumerate(zip(presets[:4], preset_labels[:4])):
        is_sel = (p_idx == idx)
        attr = curses.color_pair(8) | curses.A_BOLD if is_sel else curses.color_pair(7)
        safe_addstr(win, py, px, p_lbl, attr)
        px += len(p_lbl) + 1

    px = start_x + 4
    py = start_y + 8
    for idx, (p_code, p_lbl) in enumerate(zip(presets[4:], preset_labels[4:]), start=4):
        is_sel = (p_idx == idx)
        attr = curses.color_pair(8) | curses.A_BOLD if is_sel else curses.color_pair(7)
        safe_addstr(win, py, px, p_lbl, attr)
        px += len(p_lbl) + 1

    # Campo de texto personalizado
    custom_val = state.modal_data.get("custom_text", "")
    safe_addstr(win, start_y + 10, start_x + 4, "Tiempo manual (ej. 45m, 90, 1h20m):", curses.A_DIM)
    safe_addstr(win, start_y + 11, start_x + 4, f"[ {custom_val.ljust(18)} ]", curses.color_pair(4) | curses.A_BOLD)

    safe_addstr(win, start_y + 13, start_x + 4, "[Enter] Aplicar Preset/Texto   [Esc] Cancelar", curses.color_pair(3) | curses.A_BOLD)


def render_speedtest_modal(win, state, max_y, max_x):
    """Renderiza el modal con el test de velocidad en vivo contra Cloudflare."""
    modal_w = 60
    modal_h = 16
    start_y = max(1, (max_y - modal_h) // 2)
    start_x = max(2, (max_x - modal_w) // 2)

    for r in range(modal_h):
        safe_addstr(win, start_y + r, start_x, " " * modal_w, curses.color_pair(13))

    draw_box(win, start_y, start_x, modal_h, modal_w, "TEST DE VELOCIDAD REAL (CLOUDFLARE EDGE)", curses.color_pair(4) | curses.A_BOLD)

    st_data = state.modal_data
    is_running = st_data.get("running", False)
    phase = st_data.get("phase", "init")
    ping_ms = st_data.get("ping_ms")
    down_mbps = st_data.get("down_mbps")
    down_mbs = st_data.get("down_mbs")
    up_mbps = st_data.get("up_mbps")
    up_mbs = st_data.get("up_mbs")
    error = st_data.get("error")

    # Animación de spinner
    spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    spin_char = spinners[int(time.time() * 6) % len(spinners)]

    y = start_y + 2
    if is_running:
        safe_addstr(win, y, start_x + 4, f"{spin_char} Ejecutando test de velocidad Gigabit...", curses.color_pair(3) | curses.A_BOLD)
        safe_addstr(win, y + 1, start_x + 4, f"Fase actual: {phase.upper()} en progreso", curses.color_pair(7))
    else:
        if error:
            safe_addstr(win, y, start_x + 4, f"✖ Error: {error}", curses.color_pair(2) | curses.A_BOLD)
        else:
            safe_addstr(win, y, start_x + 4, "✔ Test de velocidad completado exitosamente.", curses.color_pair(1) | curses.A_BOLD)

    # Resultados
    box_res_y = y + 3
    draw_box(win, box_res_y, start_x + 4, 7, modal_w - 8, "MÉTRICAS CLOUDFLARE", curses.color_pair(6))

    # Latencia
    safe_addstr(win, box_res_y + 1, start_x + 7, "Latencia (Ping):", curses.A_DIM)
    if ping_ms is not None:
        safe_addstr(win, box_res_y + 1, start_x + 25, f"{ping_ms:.1f} ms", curses.color_pair(1) | curses.A_BOLD)
    else:
        safe_addstr(win, box_res_y + 1, start_x + 25, "Midiendo..." if is_running else "--", curses.color_pair(7))

    # Descarga
    safe_addstr(win, box_res_y + 2, start_x + 7, "Vel. Descarga:", curses.A_DIM)
    if down_mbps is not None:
        txt = f"{down_mbps:.1f} Mbps ({down_mbs:.1f} MB/s)"
        safe_addstr(win, box_res_y + 2, start_x + 25, txt, curses.color_pair(1) | curses.A_BOLD)
    else:
        safe_addstr(win, box_res_y + 2, start_x + 25, "Midiendo..." if is_running else "--", curses.color_pair(7))

    # Subida
    safe_addstr(win, box_res_y + 3, start_x + 7, "Vel. Subida:", curses.A_DIM)
    if up_mbps is not None:
        txt = f"{up_mbps:.1f} Mbps ({up_mbs:.1f} MB/s)"
        safe_addstr(win, box_res_y + 3, start_x + 25, txt, curses.color_pair(4) | curses.A_BOLD)
    else:
        safe_addstr(win, box_res_y + 3, start_x + 25, "Midiendo..." if is_running else "--", curses.color_pair(7))

    # Servidor
    safe_addstr(win, box_res_y + 4, start_x + 7, "Servidor Edge:", curses.A_DIM)
    safe_addstr(win, box_res_y + 4, start_x + 25, "Cloudflare Anycast (speed.cloudflare.com)", curses.color_pair(7))

    btn_text = "[ Presiona Esc o Enter para cerrar ]" if not is_running else "[ Ejecutando en segundo plano... ]"
    safe_addstr(win, start_y + modal_h - 2, start_x + 12, btn_text, curses.color_pair(4) | curses.A_BOLD)


def render_dns_modal(win, state, max_y, max_x):
    """Renderiza el modal interactivo de benchmark y selección de DNS."""
    modal_w = 68
    modal_h = 16
    start_y = max(1, (max_y - modal_h) // 2)
    start_x = max(2, (max_x - modal_w) // 2)

    for r in range(modal_h):
        safe_addstr(win, start_y + r, start_x, " " * modal_w, curses.color_pair(13))

    draw_box(win, start_y, start_x, modal_h, modal_w, "BENCHMARK Y SELECTOR DE SERVIDORES DNS", curses.color_pair(4) | curses.A_BOLD)

    dns_list = state.modal_data.get("results", [])
    sel_idx = state.modal_data.get("selected_idx", 0)
    is_benchmarking = state.modal_data.get("running", False)

    safe_addstr(win, start_y + 1, start_x + 3, " PROVEEDOR DNS              IP / SERVIDOR    LATENCIA   ESTADO", curses.color_pair(4) | curses.A_BOLD)
    safe_addstr(win, start_y + 2, start_x + 3, "─" * (modal_w - 6), curses.A_DIM)

    if is_benchmarking:
        spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spin_char = spinners[int(time.time() * 6) % len(spinners)]
        safe_addstr(win, start_y + 4, start_x + 6, f"{spin_char} Midiendo tiempos de respuesta DNS con 'dig'...", curses.color_pair(3) | curses.A_BOLD)
    else:
        for idx, item in enumerate(dns_list):
            row_y = start_y + 3 + idx
            if row_y >= start_y + modal_h - 3:
                break

            name = item["name"].ljust(25)
            ip = item["ip"].ljust(16)
            time_str = f"{item['time']} ms" if item.get("time") is not None else "Timeout"
            time_str = time_str.ljust(10)

            # Verificar si este proveedor es el que está activo en el sistema
            is_active = (item["name"].lower() in state.current_dns.lower() or (item["key"] == "adguard" and state.adblock_mode))
            act_str = "[ACTIVO]" if is_active else ""

            row_txt = f" {name} {ip} {time_str} {act_str}".ljust(modal_w - 6)

            if idx == sel_idx:
                safe_addstr(win, row_y, start_x + 3, row_txt, curses.color_pair(8) | curses.A_BOLD)
            else:
                safe_addstr(win, row_y, start_x + 3, row_txt, curses.color_pair(7))

    safe_addstr(win, start_y + modal_h - 2, start_x + 4, "[↑/↓] Seleccionar   [Enter] Aplicar Servidor   [Esc] Cerrar", curses.color_pair(3) | curses.A_BOLD)


def render_limit_modal(win, state, max_y, max_x):
    """Renderiza el modal de configuración de límites de velocidad (Token Bucket Filter)."""
    modal_w = 56
    modal_h = 14
    start_y = max(1, (max_y - modal_h) // 2)
    start_x = max(2, (max_x - modal_w) // 2)

    for r in range(modal_h):
        safe_addstr(win, start_y + r, start_x, " " * modal_w, curses.color_pair(13))

    draw_box(win, start_y, start_x, modal_h, modal_w, "CONTROL DE ANCHO DE BANDA (QOS LIMIT)", curses.color_pair(3) | curses.A_BOLD)

    # Límite actual
    cur_lim = state.rate_limit if state.rate_limit else "Ilimitado (Sin restricción)"
    safe_addstr(win, start_y + 2, start_x + 4, f"Límite configurado actualmente: {cur_lim}", curses.color_pair(1) | curses.A_BOLD)

    safe_addstr(win, start_y + 4, start_x + 4, "Selecciona una velocidad con [← / →]:", curses.color_pair(4))

    presets = ["5", "15", "30", "50", "100", "reset"]
    labels = ["[ 5 Mbps ]", "[ 15 Mbps ]", "[ 30 Mbps ]", "[ 50 Mbps ]", "[ 100 Mbps ]", "[ Sin Límite ]"]
    sel_idx = state.modal_data.get("preset_idx", 2)

    px = start_x + 4
    py = start_y + 6
    for idx, lbl in enumerate(labels[:3]):
        attr = curses.color_pair(8) | curses.A_BOLD if idx == sel_idx else curses.color_pair(7)
        safe_addstr(win, py, px, lbl, attr)
        px += len(lbl) + 1

    px = start_x + 4
    py = start_y + 8
    for idx, lbl in enumerate(labels[3:], start=3):
        attr = curses.color_pair(8) | curses.A_BOLD if idx == sel_idx else curses.color_pair(7)
        safe_addstr(win, py, px, lbl, attr)
        px += len(lbl) + 1

    safe_addstr(win, start_y + 11, start_x + 6, "[Enter] Aplicar Límite     [Esc] Cancelar", curses.color_pair(3) | curses.A_BOLD)


def render_kick_modal(win, state, max_y, max_x):
    """Renderiza el diálogo de confirmación para expulsar y bloquear un cliente."""
    modal_w = 60
    modal_h = 12
    start_y = max(1, (max_y - modal_h) // 2)
    start_x = max(2, (max_x - modal_w) // 2)

    for r in range(modal_h):
        safe_addstr(win, start_y + r, start_x, " " * modal_w, curses.color_pair(13))

    draw_box(win, start_y, start_x, modal_h, modal_w, "EXPULSIÓN Y BLOQUEO DE DISPOSITIVO", curses.color_pair(2) | curses.A_BOLD)

    target = state.modal_data.get("target_client", {})
    ip = target.get("ip", "Desconocida")
    mac = target.get("mac", "Desconocida")
    host = target.get("hostname", "Dispositivo")

    safe_addstr(win, start_y + 2, start_x + 4, "⚠ ¿Deseas expulsar y bloquear este dispositivo de la red?", curses.color_pair(3) | curses.A_BOLD)
    safe_addstr(win, start_y + 4, start_x + 6, f"Dispositivo:  {host}", curses.color_pair(7))
    safe_addstr(win, start_y + 5, start_x + 6, f"Dirección IP: {ip}", curses.color_pair(7))
    safe_addstr(win, start_y + 6, start_x + 6, f"Dirección MAC:{mac}", curses.color_pair(2) | curses.A_BOLD)

    safe_addstr(win, start_y + 8, start_x + 4, "Su MAC se agregará a la lista negra permanente del hotspot.", curses.A_DIM)

    safe_addstr(win, start_y + 10, start_x + 6, "[Enter / K] Confirmar Bloqueo     [Esc] Cancelar", curses.color_pair(2) | curses.A_BOLD)


def render_help_modal(win, state, max_y, max_x):
    """Renderiza la ventana de ayuda con la lista completa de atajos de teclado."""
    modal_w = 70
    modal_h = 20
    start_y = max(1, (max_y - modal_h) // 2)
    start_x = max(2, (max_x - modal_w) // 2)

    for r in range(modal_h):
        safe_addstr(win, start_y + r, start_x, " " * modal_w, curses.color_pair(13))

    draw_box(win, start_y, start_x, modal_h, modal_w, "GUÍA RÁPIDA DE ATAJOS Y NAVEGACIÓN", curses.color_pair(4) | curses.A_BOLD)

    shortcuts_help = [
        ("[O / Espacio]", "Encender o apagar la zona Wi-Fi (sputnik-hotspot)"),
        ("[B]", "Activar Modo Boost Turbo (0 latencia, CAKE, CPU Performance)"),
        ("[G]", "Activar o desactivar colas inteligentes CAKE (Anti-Lag / Gaming)"),
        ("[A]", "Activar o desactivar bloqueo de publicidad con AdGuard DNS"),
        ("[S]", "Modo Stealth (Ocultar o mostrar el SSID de la red Wi-Fi)"),
        ("[T]", "Abrir menú de Temporizador de apagado por tiempo o inactividad"),
        ("[L]", "Configurar Límite de ancho de banda QoS (Token Bucket Filter)"),
        ("[D]", "Benchmark y selector interactivo de proveedores DNS ultra rápidos"),
        ("[P]", "Test de velocidad real (Ping, Descarga 25MB y Subida 5MB)"),
        ("[R]", "Mostrar Código QR en pantalla para conectar celulares al instante"),
        ("[K]", "Expulsar y bloquear al dispositivo seleccionado en la tabla"),
        ("[Tab]", "Cambiar foco entre el panel de botones y la tabla de clientes"),
        ("[↑ / ↓ / ← / →]", "Navegar entre botones o desplazarse por la lista de clientes"),
        ("[Enter]", "Ejecutar la acción del botón o confirmar selecciones"),
        ("[Esc]", "Cerrar cualquier ventana o modal emergente activo"),
        ("[Q]", "Salir del Dashboard Interactivo ThinkISP"),
        ("Ratón", "Soporte completo: haz clic sobre cualquier botón o fila de cliente"),
    ]

    for idx, (key, desc) in enumerate(shortcuts_help):
        row_y = start_y + 2 + idx
        if row_y >= start_y + modal_h - 2:
            break
        safe_addstr(win, row_y, start_x + 4, key.ljust(17), curses.color_pair(3) | curses.A_BOLD)
        safe_addstr(win, row_y, start_x + 22, desc, curses.color_pair(7))

    safe_addstr(win, start_y + modal_h - 2, start_x + 18, "[ Presiona Esc o Enter para cerrar ]", curses.color_pair(4) | curses.A_BOLD)


# =====================================================================
# EJECUCIÓN DE ACCIONES
# =====================================================================
def execute_button_action(btn_id, state):
    """Ejecuta la acción asociada al botón presionado o clicado."""
    if btn_id == "toggle":
        if state.hotspot_active:
            ok, res = stop_hotspot()
            state.set_status("Zona Wi-Fi apagada con éxito.", 2)
        else:
            ok, msg = start_hotspot()
            state.set_status(f"✔ {msg}", 1)

    elif btn_id == "boost":
        ok, msg = apply_boost_mode()
        state.set_status("🚀 Modo Boost Activado: 5GHz AC | CAKE | Cero Jitter | CPU Performance", 1)

    elif btn_id == "gaming":
        new_val = not state.gaming_mode
        ok, msg = set_gaming_mode(new_val)
        state.set_status(f"✔ {msg}", 1 if new_val else 3)

    elif btn_id == "adblock":
        new_val = not state.adblock_mode
        ok, msg = set_adblock(new_val)
        state.set_status(f"✔ {msg}", 1 if new_val else 3)

    elif btn_id == "stealth":
        new_val = not state.stealth_mode
        set_stealth_mode(new_val)
        txt = "Modo Stealth ACTIVADO (SSID Oculto)." if new_val else "Modo Stealth DESACTIVADO (SSID Visible)."
        state.set_status(f"✔ {txt}", 5 if new_val else 1)

    elif btn_id == "timer":
        state.modal = MODAL_TIMER
        state.modal_data = {"preset_idx": 1, "custom_text": ""}

    elif btn_id == "limit":
        state.modal = MODAL_LIMIT
        state.modal_data = {"preset_idx": 2}

    elif btn_id == "dns":
        state.modal = MODAL_DNS
        state.modal_data = {"running": True, "results": [], "selected_idx": 0}

        def bg_dns():
            res = benchmark_dns()
            # Agregar opción auto
            res.append({"key": "auto", "name": "Auto / DHCP Router", "ip": "--", "time": 1, "desc": "Servidores por defecto"})
            state.modal_data["results"] = res
            state.modal_data["running"] = False

        threading.Thread(target=bg_dns, daemon=True).start()

    elif btn_id == "speed":
        state.modal = MODAL_SPEEDTEST
        state.modal_data = {
            "running": True,
            "phase": "ping",
            "ping_ms": None,
            "down_mbps": None,
            "down_mbs": None,
            "up_mbps": None,
            "up_mbs": None,
            "error": None
        }

        def bg_speed():
            time.sleep(0.2)
            state.modal_data["phase"] = "descarga"
            m = run_speedtest()
            state.modal_data["ping_ms"] = m.get("ping_ms")
            state.modal_data["down_mbps"] = m.get("down_mbps")
            state.modal_data["down_mbs"] = m.get("down_mbs")
            state.modal_data["up_mbps"] = m.get("up_mbps")
            state.modal_data["up_mbs"] = m.get("up_mbs")
            state.modal_data["error"] = m.get("error")
            state.modal_data["phase"] = "completado"
            state.modal_data["running"] = False

        threading.Thread(target=bg_speed, daemon=True).start()

    elif btn_id == "qr":
        state.modal = MODAL_QR
        state.modal_data = {}

    elif btn_id == "help":
        state.modal = MODAL_HELP
        state.modal_data = {}

    elif btn_id == "quit":
        state.running = False


# =====================================================================
# MANEJADOR DE ENTRADA POR RATÓN
# =====================================================================
def handle_mouse(my, mx, state):
    """Procesa eventos de clic del ratón sobre botones o filas de clientes."""
    # Si hay un modal abierto, cerrar al hacer clic fuera o procesar botones del modal
    if state.modal != MODAL_NONE:
        # Cerrar el modal al hacer clic
        if state.modal in (MODAL_QR, MODAL_HELP):
            state.modal = MODAL_NONE
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
            state.modal = MODAL_NONE
            return

        if state.modal in (MODAL_QR, MODAL_HELP):
            if ch in (10, 13, 32):  # Enter o Espacio
                state.modal = MODAL_NONE
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
                cust = state.modal_data.get("custom_text", "").strip()
                if cust:
                    if cust.startswith("idle"):
                        parts = cust.split()
                        mins = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
                        set_timer_idle(mins)
                        state.set_status(f"Apagado por inactividad configurado a {mins} min.", 1)
                    else:
                        sec = parse_duration_str(cust)
                        if sec > 0:
                            set_timer_countdown(sec)
                            state.set_status(f"Temporizador configurado para {format_duration(sec)}.", 1)
                else:
                    chosen = presets[cur_p]
                    if chosen == "cancel":
                        cancel_timer()
                        state.set_status("Temporizador cancelado.", 3)
                    elif chosen == "idle 10":
                        set_timer_idle(10)
                        state.set_status("Apagado por inactividad configurado a 10 min.", 1)
                    else:
                        sec = parse_duration_str(chosen)
                        set_timer_countdown(sec)
                        state.set_status(f"Temporizador configurado para {chosen}.", 1)

                state.modal = MODAL_NONE
            return

        elif state.modal == MODAL_LIMIT:
            presets = ["5", "15", "30", "50", "100", "reset"]
            cur_p = state.modal_data.get("preset_idx", 0)
            if ch == curses.KEY_LEFT:
                state.modal_data["preset_idx"] = max(0, cur_p - 1)
            elif ch == curses.KEY_RIGHT:
                state.modal_data["preset_idx"] = min(len(presets) - 1, cur_p + 1)
            elif ch in (10, 13):  # Enter
                val = presets[cur_p]
                if val == "reset":
                    clear_rate_limit()
                    state.set_status("Límite de velocidad eliminado (Tráfico libre).", 1)
                else:
                    set_rate_limit(val)
                    state.set_status(f"Límite de velocidad fijado en {val} Mbps.", 1)
                state.modal = MODAL_NONE
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
                    ok, msg = set_dns_provider(chosen["key"])
                    state.set_status(f"✔ {msg}", 1)
                state.modal = MODAL_NONE
            return

        elif state.modal == MODAL_KICK:
            if ch in (ord('k'), ord('K'), 10, 13):  # Confirmar expulsión
                target = state.modal_data.get("target_client", {})
                mac = target.get("mac")
                if mac:
                    ok, msg = kick_client(mac)
                    state.set_status(f"🚫 {msg}", 2)
                state.modal = MODAL_NONE
            return

        elif state.modal == MODAL_SPEEDTEST:
            if ch in (10, 13):  # Enter para cerrar cuando termine
                if not state.modal_data.get("running"):
                    state.modal = MODAL_NONE
            return

    # 2. Navegación y Atajos Globales (sin modal activo)
    # Atajos de una sola letra
    key_map = {
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

    # Atajo de tecla K para expulsar cliente seleccionado
    if ch in (ord('k'), ord('K')):
        if state.clients and 0 <= state.client_index < len(state.clients):
            state.modal = MODAL_KICK
            state.modal_data = {"target_client": state.clients[state.client_index]}
        else:
            state.set_status("No hay clientes seleccionados para expulsar.", 3)
        return

    # Tecla Tab (cambiar foco entre Botones y Clientes)
    if ch in (9, curses.KEY_BTAB):  # Tab o Shift-Tab
        state.focus = FOCUS_CLIENTS if state.focus == FOCUS_BUTTONS else FOCUS_BUTTONS
        return

    # Flechas de navegación según panel enfocado
    if state.focus == FOCUS_BUTTONS:
        avail_w = max_x - 4
        cols = 6 if avail_w >= 110 else (4 if avail_w >= 75 else 3)
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
        elif ch in (10, 13):  # Enter abre confirmación de expulsión
            if state.clients:
                state.modal = MODAL_KICK
                state.modal_data = {"target_client": state.clients[state.client_index]}


# =====================================================================
# BUCLE PRINCIPAL DEL MOTOR TUI
# =====================================================================
def run_tui(stdscr):
    """Bucle de eventos interactivo de curses en tiempo real sin parpadeo."""
    # Configurar opciones de curses
    curses.curs_set(0)
    stdscr.timeout(500)  # Redibujado cada 500 ms (2 Hz suave)
    stdscr.keypad(True)

    try:
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    except Exception:
        pass

    init_colors()
    state = TUIState()

    # Actualización inicial inmediata
    update_telemetry(state)

    while state.running:
        try:
            max_y, max_x = stdscr.getmaxyx()

            # Manejar terminales demasiado pequeños
            if max_y < 20 or max_x < 70:
                stdscr.erase()
                msg = f"Terminal muy pequeño ({max_x}x{max_y}). Por favor agranda la ventana (mínimo 75x22)."
                safe_addstr(stdscr, max_y // 2, max(1, (max_x - len(msg)) // 2), msg, curses.color_pair(3) | curses.A_BOLD)
                stdscr.noutrefresh()
                curses.doupdate()

                ch = stdscr.getch()
                if ch in (ord('q'), ord('Q'), 27):
                    break
                continue

            # Actualizar telemetría cada ciclo
            update_telemetry(state)

            # Limpiar pantalla sin parpadeo usando erase()
            stdscr.erase()

            # 1. Header & Banner
            hdr_end_y = render_header(stdscr, state, max_y, max_x)

            # 2. Panel de Estado del Sistema
            status_end_y = render_status_bar(stdscr, state, hdr_end_y, max_x)

            # 3. Métricas en Vivo de la Sesión
            metrics_end_y = render_metrics_bar(stdscr, state, status_end_y, max_x)

            # 4. Panel de Botones Interactivos
            buttons_end_y = render_buttons(stdscr, state, metrics_end_y, max_x)

            # 5. Tabla de Clientes Conectados
            render_clients_table(stdscr, state, buttons_end_y, max_y, max_x)

            # 6. Barra de Estado / Notificaciones Inferior
            render_footer(stdscr, state, max_y, max_x)

            # 7. Renderizar modal activo si existe
            if state.modal == MODAL_QR:
                render_qr_modal(stdscr, state, max_y, max_x)
            elif state.modal == MODAL_TIMER:
                render_timer_modal(stdscr, state, max_y, max_x)
            elif state.modal == MODAL_SPEEDTEST:
                render_speedtest_modal(stdscr, state, max_y, max_x)
            elif state.modal == MODAL_DNS:
                render_dns_modal(stdscr, state, max_y, max_x)
            elif state.modal == MODAL_LIMIT:
                render_limit_modal(stdscr, state, max_y, max_x)
            elif state.modal == MODAL_KICK:
                render_kick_modal(stdscr, state, max_y, max_x)
            elif state.modal == MODAL_HELP:
                render_help_modal(stdscr, state, max_y, max_x)

            # Doble buffering: actualizar pantalla física de forma limpia
            stdscr.noutrefresh()
            curses.doupdate()

            # Leer entrada del usuario con timeout no bloqueante
            ch = stdscr.getch()
            if ch == -1:
                continue

            # Manejar redimensionamiento de terminal
            if ch == curses.KEY_RESIZE:
                curses.resizeterm(*stdscr.getmaxyx())
                continue

            # Manejar eventos de ratón
            if ch == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                    if bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | curses.BUTTON1_RELEASED):
                        handle_mouse(my, mx, state)
                except curses.error:
                    pass
                continue

            # Manejar teclas de navegación y atajos
            handle_keypress(ch, state, max_x)

        except Exception as e:
            state.set_status(f"Aviso de interfaz: {e}", 3)


# =====================================================================
# FUNCIÓN PRINCIPAL DE ENTRADA
# =====================================================================
def launch_tui():
    """Punto de entrada exportado para inicializar el TUI dentro de curses.wrapper."""
    try:
        curses.wrapper(run_tui)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    launch_tui()
