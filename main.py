#!/usr/bin/env python3
"""
ThinkISP - Entrypoint Principal (Dual Mode: TUI Interactivo & CLI Scriptable)
"""

import argparse
import sys

from thinkisp.core.network import is_hotspot_active
from thinkisp.ui.cli import (
    cmd_adblock,
    cmd_band,
    cmd_blocked,
    cmd_boost,
    cmd_clients,
    cmd_dns,
    cmd_gaming,
    cmd_kick,
    cmd_limit,
    cmd_off,
    cmd_on,
    cmd_qr,
    cmd_speed,
    cmd_stats,
    cmd_status,
    cmd_stealth,
    cmd_timer,
    cmd_toggle,
    cmd_top,
    cmd_unkick,
    cmd_widget,
)


def build_parser():
    """Construye el parser de argumentos de ThinkISP."""
    parser = argparse.ArgumentParser(
        prog="thinkisp",
        description="ThinkISP - Suite de Zona Wi-Fi de Alto Rendimiento para Linux / ThinkPad"
    )
    subparsers = parser.add_subparsers(dest="command")

    # Básicos
    subparsers.add_parser("on", help="Activa la zona Wi-Fi (sputnik)")
    subparsers.add_parser("off", help="Desactiva la zona Wi-Fi y restaura la conexión previa")
    subparsers.add_parser("boost", help="Modo máxima velocidad, 0 latencia y CPU Performance")
    subparsers.add_parser("status", help="Muestra el estado y dispositivos conectados")
    subparsers.add_parser("clients", help="Lista dispositivos conectados con su señal y velocidad")

    # 1 a 4: Telemetría & Monitoreo
    subparsers.add_parser("speed", help="Test de velocidad real (Ping, Descarga y Subida)")
    subparsers.add_parser("top", aliases=["watch"], help="Monitor interactivo de tráfico en vivo por dispositivo")
    subparsers.add_parser("stats", help="Contador de datos transferidos en la sesión")
    widget_parser = subparsers.add_parser("widget", aliases=["json"], help="Salida JSON para Waybar / QuickShell")
    widget_parser.add_argument("-w", "--watch", action="store_true", help="Emitir actualizaciones continuas cada 2s")

    # 5 a 8: Seguridad & Control de Acceso
    kick_parser = subparsers.add_parser("kick", help="Expulsa y bloquea permanentemente a un intruso por IP o MAC")
    kick_parser.add_argument("target", help="IP o dirección MAC a bloquear")
    unkick_parser = subparsers.add_parser("unkick", aliases=["unblock"], help="Desbloquea una dirección MAC")
    unkick_parser.add_argument("target", help="Dirección MAC a desbloquear")
    subparsers.add_parser("blocked", aliases=["banlist"], help="Lista las direcciones MAC bloqueadas")
    stealth_parser = subparsers.add_parser("stealth", help="Modo invisible (ocultar/mostrar SSID)")
    stealth_parser.add_argument("mode", choices=["on", "off", "status"], nargs="?", default="status", help="Activar o desactivar modo stealth")
    timer_parser = subparsers.add_parser("timer", help="Temporizador de apagado por tiempo o inactividad")
    timer_parser.add_argument("action_or_duration", nargs="?", default=None, help="Duración (ej. 45m, 1h) o acción ('cancel', 'idle', 'status')")
    timer_parser.add_argument("extra_arg", nargs="?", default=None, help="Argumento adicional (ej. minutos de inactividad)")

    # 9 a 12: QoS & Optimización de Red
    gaming_parser = subparsers.add_parser("gaming", help="Modo anti-lag para juegos y VoIP (Colas CAKE)")
    gaming_parser.add_argument("mode", choices=["on", "off", "status", "toggle"], nargs="?", default="toggle", help="Activar/desactivar modo gaming")

    limit_parser = subparsers.add_parser("limit", help="Limita el ancho de banda del hotspot (ej. 30m, 50, reset)")
    limit_parser.add_argument("speed_or_action", nargs="?", default=None, help="Velocidad (ej. 30m, 50mbps) o 'reset'")

    adblock_parser = subparsers.add_parser("adblock", help="Bloqueador de anuncios a nivel de red DNS")
    adblock_parser.add_argument("mode", choices=["on", "off", "status", "toggle"], nargs="?", default="toggle", help="Activar/desactivar adblock")

    dns_parser = subparsers.add_parser("dns", help="Selector y benchmark de servidores DNS de ultra baja latencia")
    dns_parser.add_argument("provider", nargs="?", default=None, help="Proveedor ('cloudflare', 'google', 'quad9', 'adguard', 'auto', 'benchmark')")

    # Extras
    subparsers.add_parser("qr", help="Muestra el código QR para conectar móviles")
    subparsers.add_parser("toggle", help="Alterna entre encendido y apagado")
    subparsers.add_parser("tui", help="Abre el Dashboard Interactivo en terminal")
    band_parser = subparsers.add_parser("band", help="Cambia entre 5GHz y 2.4GHz")
    band_parser.add_argument("band", choices=["5g", "2.4g", "5", "2.4", "auto"], help="Banda a configurar (5g, 2.4g, auto)")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Si se invoca sin argumentos o con 'tui', lanzar el Dashboard Interactivo
    if not args.command or args.command == "tui":
        try:
            from thinkisp.ui.tui import launch_tui
            launch_tui()
            return
        except ImportError:
            # Si el TUI aún no está listo o no se puede cargar, mostrar estado
            if is_hotspot_active():
                cmd_status(args)
            else:
                parser.print_help()
            return

    if args.command == "on":
        cmd_on(args)
    elif args.command == "off":
        cmd_off(args)
    elif args.command == "boost":
        cmd_boost(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "clients":
        cmd_clients(args)
    elif args.command == "speed":
        cmd_speed(args)
    elif args.command in ("top", "watch"):
        cmd_top(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command in ("widget", "json"):
        cmd_widget(args)
    elif args.command == "kick":
        cmd_kick(args)
    elif args.command in ("unkick", "unblock"):
        cmd_unkick(args)
    elif args.command in ("blocked", "banlist"):
        cmd_blocked(args)
    elif args.command == "stealth":
        cmd_stealth(args)
    elif args.command == "timer":
        cmd_timer(args)
    elif args.command == "gaming":
        cmd_gaming(args)
    elif args.command == "limit":
        cmd_limit(args)
    elif args.command == "adblock":
        cmd_adblock(args)
    elif args.command == "dns":
        cmd_dns(args)
    elif args.command == "qr":
        cmd_qr(args)
    elif args.command == "toggle":
        cmd_toggle(args)
    elif args.command == "band":
        cmd_band(args)


if __name__ == "__main__":
    main()
