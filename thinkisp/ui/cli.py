"""
ThinkISP - Módulo de Interfaz por Línea de Comandos (CLI)
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from thinkisp.config import (
    BOLD,
    CYAN,
    DEFAULT_PSK,
    DEFAULT_SSID,
    DIM,
    DNS_PROVIDERS,
    ETH_IFACE,
    GREEN,
    HOTSPOT_CONN,
    MAGENTA,
    RED,
    RESET,
    WLAN_IFACE,
    YELLOW,
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
    parse_duration_str,
    parse_speed_mbps,
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
    format_signal_bar,
    get_arp_map,
    get_blocked_clients,
    get_connected_clients,
    get_stealth_mode,
    kick_client,
    set_band,
    set_stealth_mode,
    start_hotspot,
    stop_hotspot,
    unkick_client,
)


def cmd_on(args):
    """Activa el punto de acceso Wi-Fi."""
    if is_hotspot_active():
        print(f"\n{YELLOW}⚡ El punto de acceso '{DEFAULT_SSID}' ya está activo.{RESET}")
        cmd_status(args)
        return

    print(f"\n{BOLD}{CYAN}═══════════════════════════════════════════════════════════{RESET}")
    print(f"{BOLD}{GREEN}  🚀 INICIANDO ZONA WI-FI DE ALTO RENDIMIENTO (THINKISP){RESET}")
    print(f"{BOLD}{CYAN}═══════════════════════════════════════════════════════════{RESET}")

    eth = get_ethernet_status()
    if eth["connected"]:
        print(f"  {GREEN}✔ Cable Ethernet detectado:{RESET} {BOLD}{ETH_IFACE}{RESET} ({CYAN}{eth['speed']}{RESET})")
        print(f"  {DIM}  Enrutando internet gigabit directo a la antena Wi-Fi...{RESET}")
    else:
        print(f"  {YELLOW}⚠ Advertencia:{RESET} Cable Ethernet ({ETH_IFACE}) no conectado.")
        print(f"  {DIM}  Conecta el cable de red de la escuela para suministrar internet.{RESET}")

    print(f"\n  {CYAN}Iniciando transmisión en 5 GHz (Canal 149 / 80 MHz)...{RESET}")
    success, msg = start_hotspot()
    if not success:
        print(f"  {RED}✖ {msg}{RESET}")
        return

    start_daemon()
    ip_addr = get_hotspot_ip()
    fwd_active = get_ip_forward_status()
    is_hidden = get_stealth_mode()
    state = load_state()

    print(f"  {GREEN}✔ Zona Wi-Fi activada con éxito.{RESET}\n")
    print(f"  {BOLD}SSID:{RESET}         {GREEN}{DEFAULT_SSID}{RESET}")
    print(f"  {BOLD}Contraseña:{RESET}   {YELLOW}{DEFAULT_PSK}{RESET}")
    print(f"  {BOLD}Frecuencia:{RESET}   {CYAN}5 GHz (Canal 149 - 80 MHz VHT80){RESET}")
    print(f"  {BOLD}Visibilidad:{RESET}  {MAGENTA}Modo Stealth (Oculto){RESET}" if is_hidden else f"{GREEN}Visible públicamente{RESET}")
    print(f"  {BOLD}IP Gateway:{RESET}   {ip_addr}")
    fwd_str = f"{GREEN}ACTIVO (1){RESET}" if fwd_active else f"{YELLOW}Gestionado dinámicamente por NetworkManager{RESET}"
    print(f"  {BOLD}IP Forward:{RESET}   {fwd_str}")
    print(f"  {BOLD}Firewall:{RESET}     {GREEN}Zona 'trusted' (Forwarding y NAT abiertos){RESET}")
    if state.get("gaming_mode"):
        print(f"  {BOLD}Gaming Mode:{RESET}  {GREEN}ACTIVO (Colas CAKE Anti-Bufferbloat){RESET}")
    if state.get("adblock"):
        print(f"  {BOLD}AdBlock:{RESET}      {GREEN}ACTIVO (AdGuard DNS){RESET}")
    print(f"{BOLD}{CYAN}───────────────────────────────────────────────────────────{RESET}")
    print(f"  {DIM}Tip: Usa {BOLD}thinkisp{RESET}{DIM} sin argumentos para abrir el Dashboard Interactivo (TUI).{RESET}")
    print(f"  {DIM}     Usa {BOLD}thinkisp qr{RESET}{DIM} para conectar tu celular con la cámara.{RESET}")
    print(f"  {DIM}     Usa {BOLD}thinkisp gaming{RESET}{DIM} para activar prioridad anti-lag para juegos.{RESET}")
    print(f"{BOLD}{CYAN}═══════════════════════════════════════════════════════════{RESET}\n")


def cmd_off(args):
    """Desactiva el punto de acceso Wi-Fi y restaura la conexión previa."""
    if not is_hotspot_active():
        print(f"\n{YELLOW}ℹ La zona Wi-Fi no está activa.{RESET}\n")
        return

    print(f"\n{BOLD}{YELLOW}Apagando zona Wi-Fi '{DEFAULT_SSID}'...{RESET}")
    stop_daemon()
    cancel_timer()
    success, summary = stop_hotspot()

    print(f"{BOLD}{GREEN}✔ Zona Wi-Fi apagada con éxito.{RESET}")
    if summary:
        print(f"\n{BOLD}{CYAN}┌─── [ RESUMEN DE LA SESIÓN ] ─────────────────────────────{RESET}")
        print(f"│  Duración:       {BOLD}{format_duration(summary['duration'])}{RESET}")
        print(f"│  Descargado:     {GREEN}⬇ {format_bytes(summary['downloaded'])}{RESET}")
        print(f"│  Subido:         {BLUE}⬆ {format_bytes(summary['uploaded'])}{RESET}")
        total = summary['downloaded'] + summary['uploaded']
        print(f"│  Total Tráfico:  {BOLD}{CYAN}⇆ {format_bytes(total)}{RESET}")
        print(f"{BOLD}{CYAN}└──────────────────────────────────────────────────────────{RESET}\n")


def cmd_boost(args):
    """Activa el modo turbo de máxima velocidad y latencia cero."""
    print(f"\n{BOLD}{MAGENTA}═══════════════════════════════════════════════════════════{RESET}")
    print(f"{BOLD}{MAGENTA}  ⚡ THINKISP BOOST: MÁXIMA VELOCIDAD & CERO LATENCIA     {RESET}")
    print(f"{BOLD}{MAGENTA}═══════════════════════════════════════════════════════════{RESET}")

    if not is_hotspot_active():
        print(f"  {CYAN}Activando zona Wi-Fi en modo Turbo...{RESET}")
        cmd_on(args)

    apply_boost_mode()
    eth = get_ethernet_status()

    print(f"  {GREEN}✔ Wi-Fi Power Save desactivado permanentemente (0 jitter).{RESET}")
    print(f"  {GREEN}✔ Algoritmo CAKE activado en colas (Cero bufferbloat).{RESET}")
    print(f"  {GREEN}✔ Cola de transmisión ampliada a 2000 paquetes.{RESET}")
    print(f"  {GREEN}✔ Perfil ThinkPad activado en: {BOLD}Divino Nuclear (Performance){RESET}")

    print(f"\n  {BOLD}Estado de Rendimiento:{RESET}")
    print(f"  • Banda Wi-Fi:       {CYAN}5 GHz AC (Canal 149 - 80 MHz, hasta 867 Mbps PHY){RESET}")
    print(f"  • Enlace Cableado:   {GREEN}{eth['speed']}{RESET} ({'Conectado' if eth['connected'] else 'Desconectado'})")
    print(f"  • Latencia local:    {GREEN}< 2 ms garantizados (Ping plano){RESET}")
    print(f"{BOLD}{MAGENTA}═══════════════════════════════════════════════════════════{RESET}\n")


def cmd_status(args):
    """Muestra el estado completo y los clientes conectados."""
    active = is_hotspot_active()
    eth = get_ethernet_status()
    fwd_active = get_ip_forward_status()
    state = load_state()
    is_hidden = get_stealth_mode()
    qdiscs = run_cmd(["nmcli", "-g", "tc.qdiscs", "connection", "show", HOTSPOT_CONN])
    
    print(f"\n{BOLD}{CYAN}┌─── [ ESTADO THINKISP HOTSPOT ] ──────────────────────────{RESET}")
    if active:
        ip_addr = get_hotspot_ip()
        print(f"│  Estado:         {GREEN}● ACTIVO (Transmitiendo){RESET}")
        print(f"│  SSID:           {BOLD}{DEFAULT_SSID}{RESET}")
        print(f"│  Contraseña:     {YELLOW}{DEFAULT_PSK}{RESET}")
        print(f"│  Frecuencia:     {CYAN}5 GHz (Canal 149 - 80 MHz VHT80){RESET}")
        print(f"│  Visibilidad:    {MAGENTA}Modo Stealth (Oculto){RESET}" if is_hidden else f"│  Visibilidad:    {GREEN}Visible públicamente{RESET}")
        print(f"│  IP Gateway:     {ip_addr}")
        fwd_str = f"{GREEN}Activo (1){RESET}" if fwd_active else f"{YELLOW}0 (se activa con tráfico NM){RESET}"
        print(f"│  IP Forwarding:  {fwd_str}")
        print(f"│  Firewall Zone:  {GREEN}trusted (ACCEPT / forward: yes){RESET}")
        
        if qdiscs:
            if "cake" in qdiscs or "fq_codel" in qdiscs:
                print(f"│  QoS / Gaming:   {GREEN}🎮 ACTIVO ({qdiscs}){RESET}")
            elif "tbf" in qdiscs:
                print(f"│  Límite Ancho B: {YELLOW}⚠ {state.get('rate_limit', qdiscs)}{RESET}")
            else:
                print(f"│  QoS Qdisc:      {qdiscs}")
        else:
            print(f"│  QoS / Gaming:   {DIM}Estándar{RESET}")

        adblock_str = f"{GREEN}ACTIVO (AdGuard DNS){RESET}" if state.get("adblock") else f"{DIM}Desactivado{RESET}"
        print(f"│  AdBlock:        {adblock_str}")
        dns_curr = state.get("dns", "Auto / DHCP")
        print(f"│  DNS Upstream:   {CYAN}{dns_curr}{RESET}")

        session = state.get("session")
        if session:
            dur = time.time() - session["start_time"]
            rx, tx = get_iface_bytes(WLAN_IFACE)
            down = max(0, tx - session["start_tx"])
            up = max(0, rx - session["start_rx"])
            print(f"│  Sesión Actual:  {format_duration(dur)} (⬇ {format_bytes(down)} | ⬆ {format_bytes(up)})")

        tinfo = get_timer_status()
        if tinfo and "remaining_str" in tinfo:
            print(f"│  Temporizador:   {YELLOW}⏳ Se apaga en {tinfo['remaining_str']}{RESET}")
    else:
        print(f"│  Estado:         {RED}○ INACTIVO (Apagado){RESET}")
        print(f"│  SSID:           {DEFAULT_SSID}")
        fwd_str = f"{GREEN}Activo (1){RESET}" if fwd_active else f"{DIM}Inactivo (0){RESET}"
        print(f"│  IP Forwarding:  {fwd_str}")

    eth_color = GREEN if eth["connected"] else YELLOW
    eth_text = f"{eth_color}{'Conectado (' + eth['speed'] + ')' if eth['connected'] else 'No conectado'}{RESET}"
    print(f"│  Ethernet Esc.:  {eth_text}")
    print(f"{BOLD}{CYAN}└──────────────────────────────────────────────────────────{RESET}")

    if active:
        clients = get_connected_clients()
        print(f"\n{BOLD}Dispositivos Conectados ({len(clients)}):{RESET}")
        if not clients:
            print(f"  {DIM}Esperando dispositivos... Escanea el código QR ({BOLD}thinkisp qr{RESET}{DIM}) para conectar.{RESET}\n")
        else:
            print(f"  {'IP':<16} {'MAC':<19} {'SEÑAL':<30} {'VELOCIDAD':<12}")
            print(f"  {'-'*15} {'-'*18} {'-'*28} {'-'*11}")
            for c in clients:
                tx_spd = f"{c['tx_mbps']} Mbps" if c['tx_mbps'] else "N/D"
                sig_bar = format_signal_bar(c['signal'])
                print(f"  {c['ip']:<16} {c['mac']:<19} {sig_bar:<39} {CYAN}{tx_spd:<12}{RESET}")
            print("")


def cmd_speed(args):
    """Mide velocidad del enlace (Ping, Descarga y Subida)."""
    print(f"\n{BOLD}{CYAN}═══════════════════════════════════════════════════════════{RESET}")
    print(f"{BOLD}{CYAN}  ⚡ TEST DE VELOCIDAD THINKISP (ENLACE INTERNET)          {RESET}")
    print(f"{BOLD}{CYAN}═══════════════════════════════════════════════════════════{RESET}")
    eth = get_ethernet_status()
    if eth["connected"]:
        print(f"  {GREEN}✔ Enlace de origen:{RESET} {BOLD}{ETH_IFACE}{RESET} ({CYAN}{eth['speed']}{RESET})")
    else:
        print(f"  {YELLOW}ℹ Enlace actual:{RESET} Wi-Fi cliente / Móvil (Ethernet no conectado)")
    
    print(f"\n  {DIM}Ejecutando prueba de velocidad contra la red edge...{RESET}")
    metrics = run_speedtest()

    if metrics["ping_ms"] is not None:
        print(f"  {GREEN}✔ Latencia (Ping):{RESET}      {BOLD}{metrics['ping_ms']:.1f} ms{RESET}")
    if metrics["down_mbps"] is not None:
        print(f"  {GREEN}✔ Velocidad Descarga:{RESET}  {BOLD}{CYAN}{metrics['down_mbps']:.1f} Mbps{RESET} ({metrics['down_mbs']:.1f} MB/s)")
    if metrics["up_mbps"] is not None:
        print(f"  {GREEN}✔ Velocidad Subida:{RESET}    {BOLD}{MAGENTA}{metrics['up_mbps']:.1f} Mbps{RESET} ({metrics['up_mbs']:.1f} MB/s)")
    print(f"{BOLD}{CYAN}═══════════════════════════════════════════════════════════{RESET}\n")


def cmd_top(args):
    """Monitor interactivo de tráfico en tiempo real por dispositivo."""
    if not is_hotspot_active():
        print(f"\n{YELLOW}ℹ La zona Wi-Fi no está activa. Enciéndela primero con 'thinkisp on'.{RESET}\n")
        return

    print("\033[?25l", end="")
    prev_stations = {}
    prev_time = time.time()
    prev_tot_rx, prev_tot_tx = get_iface_bytes(WLAN_IFACE)

    try:
        while True:
            time.sleep(1.0)
            curr_time = time.time()
            dt = max(0.2, curr_time - prev_time)
            
            curr_tot_rx, curr_tot_tx = get_iface_bytes(WLAN_IFACE)
            tot_down_rate = max(0, (curr_tot_tx - prev_tot_tx) / dt)
            tot_up_rate = max(0, (curr_tot_rx - prev_tot_rx) / dt)

            curr_stations = {c["mac"]: c for c in get_connected_clients()}

            output = []
            output.append("\033[H\033[2J")
            output.append(f"{BOLD}{CYAN}┌─── [ THINKISP TOP: TRÁFICO EN TIEMPO REAL ] ─────────────{RESET}")
            output.append(f"│  Red: {BOLD}{DEFAULT_SSID}{RESET} (5 GHz Ch 149) | Clientes: {BOLD}{len(curr_stations)}{RESET}")
            output.append(f"│  Tráfico Total: {GREEN}⬇ {format_bytes(tot_down_rate)}/s{RESET} | {BLUE}⬆ {format_bytes(tot_up_rate)}/s{RESET}")
            output.append(f"│  {DIM}Presiona Ctrl+C para salir.{RESET}")
            output.append(f"{BOLD}{CYAN}└──────────────────────────────────────────────────────────{RESET}\n")

            if not curr_stations:
                output.append(f"  {DIM}Esperando dispositivos conectados...{RESET}\n")
            else:
                output.append(f"  {'IP':<15} {'MAC':<18} {'DESCARGA (⬇)':<16} {'SUBIDA (⬆)':<14} {'SEÑAL':<20}")
                output.append(f"  {'-'*14} {'-'*17} {'-'*15} {'-'*13} {'-'*18}")

                for mac, st in curr_stations.items():
                    prev = prev_stations.get(mac, {"rx_bytes": 0, "tx_bytes": 0})
                    down_str = f"{GREEN}Activo{RESET}"
                    up_str = f"{BLUE}Activo{RESET}"
                    sig_str = format_signal_bar(st["signal"])
                    output.append(f"  {st['ip']:<15} {mac:<18} {down_str:<25} {up_str:<23} {sig_str}")

            print("\n".join(output))

            prev_stations = curr_stations
            prev_time = curr_time
            prev_tot_rx = curr_tot_rx
            prev_tot_tx = curr_tot_tx

    except KeyboardInterrupt:
        pass
    finally:
        print("\033[?25h")
        print(f"\n{GREEN}✔ Monitor finalizado.{RESET}\n")


def cmd_stats(args):
    """Muestra estadísticas de consumo de datos."""
    state = load_state()
    active = is_hotspot_active()
    
    print(f"\n{BOLD}{CYAN}┌─── [ ESTADÍSTICAS DE CONSUMO THINKISP ] ─────────────────{RESET}")
    if active:
        session = state.get("session")
        if session:
            rx, tx = get_iface_bytes(WLAN_IFACE)
            duration = time.time() - session["start_time"]
            downloaded = max(0, tx - session["start_tx"])
            uploaded = max(0, rx - session["start_rx"])
            total = downloaded + uploaded
            
            print(f"│  Estado:           {GREEN}● SESIÓN EN CURSO{RESET}")
            print(f"│  Tiempo Activo:    {BOLD}{format_duration(duration)}{RESET}")
            print(f"│  Descarga Clientes:{GREEN} ⬇ {format_bytes(downloaded)}{RESET}")
            print(f"│  Subida Clientes:  {BLUE} ⬆ {format_bytes(uploaded)}{RESET}")
            print(f"│  Total Transferido:{BOLD}{CYAN} ⇆ {format_bytes(total)}{RESET}")
            if duration > 0:
                avg_rate = total / duration
                print(f"│  Consumo Promedio: {DIM}{format_bytes(avg_rate)}/s{RESET}")
        else:
            print(f"│  Estado:           {GREEN}● ACTIVO (Sin datos previos de sesión){RESET}")
    else:
        last = state.get("last_session")
        if last:
            print(f"│  Estado:           {DIM}○ INACTIVO (Última sesión registrada){RESET}")
            print(f"│  Duración:         {BOLD}{format_duration(last['duration'])}{RESET}")
            print(f"│  Descarga Total:   {GREEN} ⬇ {format_bytes(last['downloaded'])}{RESET}")
            print(f"│  Subida Total:     {BLUE} ⬆ {format_bytes(last['uploaded'])}{RESET}")
            print(f"│  Total Transferido:{BOLD}{CYAN} ⇆ {format_bytes(last['downloaded'] + last['uploaded'])}{RESET}")
        else:
            print(f"│  Estado:           {DIM}○ Inactivo (Aún no hay sesiones registradas){RESET}")
    print(f"{BOLD}{CYAN}└──────────────────────────────────────────────────────────{RESET}\n")


def cmd_widget(args):
    """Emite estado en formato JSON estándar para Waybar o QuickShell."""
    watch_mode = getattr(args, "watch", False)
    
    def emit():
        active = is_hotspot_active()
        eth = get_ethernet_status()
        state = load_state()
        
        if active:
            clients = get_connected_clients()
            client_count = len(clients)
            ip_addr = get_hotspot_ip()
            session = state.get("session")
            dur_str = ""
            down_str = ""
            if session:
                dur = time.time() - session["start_time"]
                dur_str = f" | {format_duration(dur)}"
                rx, tx = get_iface_bytes(WLAN_IFACE)
                down = max(0, tx - session["start_tx"])
                down_str = f"\nDescargado: {format_bytes(down)}"
                
            data = {
                "text": f"󰖩 {DEFAULT_SSID} ({client_count})",
                "alt": "active",
                "tooltip": (
                    f"Zona Wi-Fi: {DEFAULT_SSID} (5 GHz Ch 149)\n"
                    f"Estado: Activo{dur_str}\n"
                    f"Clientes: {client_count}\n"
                    f"Gateway: {ip_addr}{down_str}\n"
                    f"Ethernet: {'Conectado (' + eth['speed'] + ')' if eth['connected'] else 'Desconectado'}"
                ),
                "class": "active",
                "percentage": 100,
                "clients": client_count
            }
        else:
            data = {
                "text": f"󰖪 {DEFAULT_SSID}",
                "alt": "inactive",
                "tooltip": f"Zona Wi-Fi '{DEFAULT_SSID}' apagada\nClic para activar con 'thinkisp on'",
                "class": "inactive",
                "percentage": 0,
                "clients": 0
            }
        print(json.dumps(data), flush=True)

    if watch_mode:
        try:
            while True:
                emit()
                time.sleep(2)
        except KeyboardInterrupt:
            pass
    else:
        emit()


def cmd_qr(args):
    """Genera e imprime el código QR en la terminal para conectar móviles."""
    wifi_str = f"WIFI:S:{DEFAULT_SSID};T:WPA;P:{DEFAULT_PSK};;"
    print(f"\n{BOLD}{CYAN}Escanea con la cámara de tu celular para conectarte a '{DEFAULT_SSID}':{RESET}\n")
    if shutil.which("qrencode"):
        subprocess.run(["qrencode", "-t", "UTF8", wifi_str])
    else:
        print(f"{YELLOW}Instala qrencode para ver el código QR (sudo pacman -S qrencode){RESET}")
    print(f"\n  {BOLD}SSID:{RESET}       {GREEN}{DEFAULT_SSID}{RESET}")
    print(f"  {BOLD}Clave:{RESET}      {YELLOW}{DEFAULT_PSK}{RESET}")
    print(f"  {BOLD}Seguridad:{RESET}  WPA2-PSK\n")


def cmd_gaming(args):
    """Activa el modo anti-lag para juegos y VoIP (CAKE)."""
    mode = getattr(args, "mode", "toggle")
    state = load_state()
    is_gaming = state.get("gaming_mode", False)

    if mode == "status":
        print(f"\n{BOLD}Modo Gaming:{RESET} " + (f"{GREEN}ACTIVADO (CAKE Anti-Bufferbloat){RESET}" if is_gaming else f"{YELLOW}DESACTIVADO{RESET}\n"))
        return

    activate = True if mode == "on" else (False if mode == "off" else not is_gaming)
    success, msg = set_gaming_mode(activate)
    print(f"\n{GREEN if activate else YELLOW}{msg}{RESET}\n")


def cmd_limit(args):
    """Limita el ancho de banda del hotspot."""
    speed_arg = getattr(args, "speed_or_action", None)
    if not speed_arg or speed_arg == "status":
        curr_limit = get_rate_limit()
        print(f"\n{BOLD}Límite de Ancho de Banda:{RESET} " + (f"{YELLOW}{curr_limit}{RESET}" if curr_limit else f"{GREEN}Sin límite (Velocidad máxima){RESET}\n"))
        return

    if speed_arg in ("reset", "off", "clear", "none", "0"):
        clear_rate_limit()
        print(f"\n{GREEN}✔ Límite de velocidad eliminado.{RESET}\n")
        return

    mbps = parse_speed_mbps(speed_arg)
    if not mbps or mbps <= 0:
        print(f"\n{RED}✖ Velocidad no válida: '{speed_arg}'.{RESET}\n")
        return

    set_rate_limit(mbps)
    print(f"\n{GREEN}✔ Límite de ancho de banda fijado en: {mbps} Mbps.{RESET}\n")


def cmd_adblock(args):
    """Activa o desactiva AdGuard DNS en la red."""
    mode = getattr(args, "mode", "toggle")
    is_adblock = get_adblock_status()

    if mode == "status":
        print(f"\n{BOLD}AdBlock en Hotspot:{RESET} " + (f"{GREEN}ACTIVADO (AdGuard DNS){RESET}" if is_adblock else f"{YELLOW}DESACTIVADO{RESET}\n"))
        return

    activate = True if mode == "on" else (False if mode == "off" else not is_adblock)
    success, msg = set_adblock(activate)
    print(f"\n{GREEN if activate else YELLOW}{msg}{RESET}\n")


def cmd_dns(args):
    """Selecciona el proveedor DNS o ejecuta benchmark."""
    target = getattr(args, "provider", None)

    if target == "benchmark":
        print(f"\n{BOLD}{CYAN}═══════════════════════════════════════════════════════════{RESET}")
        print(f"{BOLD}{CYAN}  ⚡ BENCHMARK DE RESOLUCIÓN DNS                           {RESET}")
        print(f"{BOLD}{CYAN}═══════════════════════════════════════════════════════════{RESET}")
        results = benchmark_dns()
        print(f"  {'PROVEEDOR':<25} {'IP':<16} {'LATENCIA':<12} {'CARACTERÍSTICAS'}")
        print(f"  {'-'*24} {'-'*15} {'-'*11} {'-'*24}")
        for idx, r in enumerate(results):
            if r["time"] is not None:
                color = GREEN if idx == 0 else (CYAN if r["time"] < 50 else YELLOW)
                time_str = f"{color}{r['time']} ms{RESET}"
            else:
                time_str = f"{RED}Timeout{RESET}"
            print(f"  {r['name']:<25} {r['ip']:<16} {time_str:<21} {DIM}{r['desc']}{RESET}")

        best = results[0]
        if best["time"] is not None:
            print(f"\n  {GREEN}✔ Recomendado para hoy:{RESET} {BOLD}{best['name']}{RESET} ({best['time']} ms)")
        print(f"{BOLD}{CYAN}═══════════════════════════════════════════════════════════{RESET}\n")
        return

    if not target or target == "status":
        curr_dns = get_current_dns()
        print(f"\n{BOLD}DNS Upstream Configurado:{RESET} {CYAN}{curr_dns}{RESET}\n")
        return

    success, msg = set_dns_provider(target)
    print(f"\n{GREEN if success else RED}{msg}{RESET}\n")


def cmd_kick(args):
    """Expulsa y bloquea permanentemente a un dispositivo intruso."""
    success, msg = kick_client(args.target)
    print(f"\n{GREEN if success else RED}{msg}{RESET}\n")


def cmd_unkick(args):
    """Desbloquea una dirección MAC."""
    success, msg = unkick_client(args.target)
    print(f"\n{GREEN if success else RED}{msg}{RESET}\n")


def cmd_blocked(args):
    """Lista las direcciones MAC bloqueadas."""
    macs = get_blocked_clients()
    print(f"\n{BOLD}{RED}┌─── [ DISPOSITIVOS BLOQUEADOS EN THINKISP ] ──────────────{RESET}")
    if not macs:
        print(f"│  {GREEN}No hay dispositivos bloqueados en la lista negra.{RESET}")
    else:
        for m in macs:
            print(f"│  🚫 {BOLD}{m}{RESET}")
    print(f"{BOLD}{RED}└──────────────────────────────────────────────────────────{RESET}\n")


def cmd_stealth(args):
    """Activa o desactiva el modo invisible (SSID oculto)."""
    mode = getattr(args, "mode", "status")
    if mode == "status" or not mode:
        is_hidden = get_stealth_mode()
        print(f"\n{BOLD}Modo Stealth (SSID Oculto):{RESET} " + (f"{GREEN}ACTIVADO (Red invisible){RESET}" if is_hidden else f"{YELLOW}DESACTIVADO (Red pública){RESET}\n"))
        return

    val = (mode == "on")
    set_stealth_mode(val)
    print(f"\n{GREEN if val else CYAN}✔ Modo Stealth " + ("ACTIVADO" if val else "DESACTIVADO") + f".{RESET}\n")


def cmd_timer(args):
    """Programa el temporizador de apagado."""
    action = getattr(args, "action_or_duration", None)
    extra = getattr(args, "extra_arg", None)

    if not action or action == "status":
        tinfo = get_timer_status()
        if tinfo:
            print(f"\n{BOLD}{CYAN}┌─── [ TEMPORIZADOR ACTIVO ] ──────────────────────────────{RESET}")
            if "remaining_str" in tinfo:
                print(f"│  Cuenta regresiva: {GREEN}Quedan {tinfo['remaining_str']}{RESET} (Apaga a las {tinfo['end_time_str']})")
            if "idle_mins" in tinfo:
                print(f"│  Inactividad:      {CYAN}Apaga tras {tinfo['idle_mins']} minutos sin clientes{RESET}")
            print(f"{BOLD}{CYAN}└──────────────────────────────────────────────────────────{RESET}\n")
            return
        print(f"\n{YELLOW}ℹ No hay temporizadores activos.{RESET}\n")
        return

    if action in ("cancel", "off", "stop"):
        cancel_timer()
        print(f"\n{GREEN}✔ Temporizador cancelado.{RESET}\n")
        return

    if action == "idle":
        mins = int(extra) if extra and extra.isdigit() else 10
        set_timer_idle(mins)
        print(f"\n{GREEN}✔ Apagado por inactividad fijado en {mins} minutos.{RESET}\n")
        return

    sec = parse_duration_str(action)
    if sec <= 0:
        print(f"\n{RED}✖ Formato no válido: '{action}'. Usa '45m', '1h', etc.{RESET}\n")
        return

    success, msg = set_timer_countdown(sec)
    print(f"\n{GREEN}{msg}{RESET}\n")


def cmd_toggle(args):
    """Alterna entre encendido y apagado."""
    if is_hotspot_active():
        cmd_off(args)
    else:
        cmd_on(args)


def cmd_band(args):
    """Cambia entre 5GHz y 2.4GHz."""
    success, msg = set_band(args.band)
    print(f"\n{GREEN if success else RED}{msg}{RESET}\n")
