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
    BLUE,
    BOLD,
    CYAN,
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
    wifi_qr_payload,
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
    recover_network,
)

from thinkisp.core.profile import hotspot_details
from thinkisp.ui.setup import ensure_setup, configure_hotspot


def cmd_setup(args):
    configure_hotspot()


def cmd_network(args):
    from thinkisp.core.deployment import inspect_deployment, store_deployment, report_lines
    from thinkisp.core.topology import NetworkIntent, evaluate_plan
    report = inspect_deployment()
    if args.network_action == 'plan':
        values = dict(report['saved_intent'])
        for arg, key in [('ap', 'ap_interface'), ('upstream', 'upstream_interface'), ('mode', 'mode'), ('engine', 'portal_engine')]:
            value = getattr(args, arg, None)
            if value is not None:
                values[key] = None if key.endswith('_interface') and value == 'auto' else value
        intent = NetworkIntent(**values).validate()
        report['plan'] = evaluate_plan(report['inventory'], intent, fallback_ap=WLAN_IFACE)
    report['saved'] = False
    if getattr(args, 'save', False):
        store_deployment(report)
        report['saved'] = True
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print('\n'.join(report_lines(report)))
        if report['saved']:
            print('Plan guardado por perfil. No se cambió la red ni se activó el portal.')
    if not report['plan']['configuration_valid']:
        raise ThinkISPError('El plan tiene incompatibilidades. Consulta el informe anterior.')


def cmd_doctor(args):
    from thinkisp.core.doctor import collect_report
    report = collect_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print('thinkISP · Diagnóstico de compatibilidad (sólo lectura)')
        labels = {'ok': 'OK', 'warning': 'AVISO', 'error': 'ERROR', 'unverified': 'SIN PROBAR'}
        for check in report['checks']:
            print(f"[{labels[check['status']]}] {check['id']}: {check['detail']}")
        print('No se activó el hotspot ni se modificaron DNS, perfiles o reglas de red.')
    if not report['core_checks_passed']:
        raise ThinkISPError('Hay requisitos pendientes o sin confirmar. Consulta el diagnóstico anterior.')


def cmd_recover(args):
    print(_checked(recover_network()))


def _checked(result):
    success, message = result
    if not success:
        raise ThinkISPError(str(message))
    return message


def cmd_on(args):
    """Configura la primera ejecución y activa el AP del equipo detectado."""
    ensure_setup()
    print(f"\n{BOLD}{CYAN}INICIANDO ZONA WI-FI · thinkISP{RESET}")
    _checked(start_hotspot())
    start_daemon()
    data = hotspot_details()
    print(f"{GREEN}Zona Wi-Fi activada con éxito.{RESET}")
    print(f"SSID: {data['ssid']}")
    print(f"Interfaz: {data['interface']} · Radio: {data['radio']}")
    print(f"Seguridad: {data['security']} · Oculto: {'sí' if data['hidden'] else 'no'}")
    print(f"Gateway: {get_hotspot_ip()}")
    print("Usa 'thinkisp qr' para consultar las credenciales y el código de conexión.")


def cmd_off(args):
    """Confirma el apagado antes de anunciarlo y de cerrar el vigilante."""
    summary = _checked(stop_hotspot())
    stop_daemon()
    print(f"{GREEN}Zona Wi-Fi apagada.{RESET}")
    if isinstance(summary, dict):
        print(f"Duración: {format_duration(summary['duration'])}")
        print(f"Descargado: {format_bytes(summary['downloaded'])}")
        print(f"Subido: {format_bytes(summary['uploaded'])}")


def cmd_boost(args):
    if not is_hotspot_active(check=True):
        cmd_on(args)
    print(_checked(apply_boost_mode()))
    data = hotspot_details()
    print(f"Interfaz: {data['interface']} · Radio: {data['radio']}")


def cmd_status(args):
    """Muestra datos consultados al sistema, sin suponer banda ni firewall."""
    active = is_hotspot_active(check=True)
    data = hotspot_details()
    eth = get_ethernet_status()
    print(f"\n{BOLD}thinkISP · ESTADO{RESET}")
    print(f"Estado: {'ACTIVO' if active else 'INACTIVO'}")
    print(f"SSID: {data['ssid']} · Interfaz: {data['interface']}")
    print(f"Radio: {data['radio']} · Potencia: {data.get('tx_power', 'Sin datos')}")
    print(f"Seguridad: {data['security']} · SSID oculto: {'sí' if data['hidden'] else 'no'}")
    print(f"Gateway: {get_hotspot_ip() if active else 'Sin datos'}")
    print(f"Zona de firewall del perfil: {data['firewall']}")
    print(f"IPv4 forwarding: {'Activo' if get_ip_forward_status() else 'Inactivo'}")
    print(f"Salida: {eth['interface'] or 'No detectada'} · {'Conectada' if eth['connected'] else 'Desconectada'} · {eth['speed']}")
    if active and WLAN_IFACE:
        qdiscs = run_cmd(['tc', 'qdisc', 'show', 'dev', WLAN_IFACE])
        print(f"Colas aplicadas: {qdiscs or 'Sin datos'}")
        cmd_clients(args)


def cmd_clients(args):
    """Lista dispositivos conectados con su señal y velocidad."""
    clients = get_connected_clients(check=True)
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
        print(f"  {GREEN}✔ Enlace de origen:{RESET} {BOLD}{eth.get('interface') or 'Sin datos'}{RESET} ({CYAN}{eth['speed']}{RESET})")
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
    if metrics["error"]:
        raise ThinkISPError(metrics["error"])
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
            output.append(f"│  Red: {BOLD}{hotspot_details()['ssid']}{RESET} | Interfaz: {WLAN_IFACE} | Clientes: {len(curr_stations)}")
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
                    down_str = "No medido"
                    up_str = "No medido"
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
        info = hotspot_details()
        
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
                "text": f"󰖩 {info['ssid']} ({client_count})",
                "alt": "active",
                "tooltip": (
                    f"Zona Wi-Fi: {info['ssid']} ({info['radio']})\n"
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
                "text": f"󰖪 {info['ssid']}",
                "alt": "inactive",
                "tooltip": f"Zona Wi-Fi '{info['ssid']}' apagada\nClic para activar con 'thinkisp on'",
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
    data = hotspot_details(secrets=True)
    payload = wifi_qr_payload(data['ssid'], data['psk'], data['hidden'], data['security'])
    print(f"SSID: {data['ssid']}")
    print(f"Contraseña: {data['psk']}")
    print(f"Seguridad: {data['security']}")
    if shutil.which('qrencode'):
        print(run_cmd(['qrencode', '-t', 'UTF8', payload], check=True))
    else:
        print('Instala qrencode para generar el código QR en esta terminal.')


def cmd_gaming(args):
    """Activa el modo anti-lag para juegos y VoIP (CAKE)."""
    mode = getattr(args, "mode", "toggle")
    state = load_state()
    is_gaming = state.get("gaming_mode", False)

    if mode == "status":
        print(f"\n{BOLD}Modo Gaming:{RESET} " + (f"{GREEN}ACTIVADO (CAKE Anti-Bufferbloat){RESET}" if is_gaming else f"{YELLOW}DESACTIVADO{RESET}\n"))
        return

    activate = True if mode == "on" else (False if mode == "off" else not is_gaming)
    msg = _checked(set_gaming_mode(activate))
    print(f"\n{GREEN if activate else YELLOW}{msg}{RESET}\n")


def cmd_limit(args):
    """Limita el ancho de banda del hotspot."""
    speed_arg = getattr(args, "speed_or_action", None)
    if not speed_arg or speed_arg == "status":
        curr_limit = get_rate_limit()
        print(f"\n{BOLD}Límite de Ancho de Banda:{RESET} " + (f"{YELLOW}{curr_limit}{RESET}" if curr_limit else f"{GREEN}Sin límite (Velocidad máxima){RESET}\n"))
        return

    if speed_arg in ("reset", "off", "clear", "none", "0"):
        print(_checked(clear_rate_limit()))
        return

    mbps = parse_speed_mbps(speed_arg)
    if not mbps or mbps <= 0:
        raise ThinkISPError(f"Velocidad no válida: {speed_arg!r}.")

    print(_checked(set_rate_limit(mbps)))


def cmd_adblock(args):
    """Activa o desactiva AdGuard DNS en la red."""
    mode = getattr(args, "mode", "toggle")
    is_adblock = get_adblock_status()

    if mode == "status":
        print(f"\n{BOLD}AdGuard DNS en la salida:{RESET} " + (f"{GREEN}APLICADO (AdGuard DNS){RESET}" if is_adblock else f"{YELLOW}DESACTIVADO{RESET}\n"))
        return

    activate = True if mode == "on" else (False if mode == "off" else not is_adblock)
    msg = _checked(set_adblock(activate))
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

    print(_checked(set_dns_provider(target)))


def cmd_kick(args):
    """Expulsa y bloquea permanentemente a un dispositivo intruso."""
    print(_checked(kick_client(args.target)))


def cmd_unkick(args):
    """Desbloquea una dirección MAC."""
    print(_checked(unkick_client(args.target)))


def cmd_blocked(args):
    macs = get_blocked_clients()
    print('Lista configurada de acceso bloqueado al router e Internet:')
    for mac in macs:
        print(f'  {mac}')
    if not macs:
        print('  Sin bloqueos configurados.')
    print('La asociación Wi-Fi y el aislamiento entre clientes son controles distintos.')


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
        raise ThinkISPError(f"Duración no válida: {action!r}.")

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
    print(_checked(set_band(args.band)))
