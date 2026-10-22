#!/usr/bin/env python3
"""
ThinkISP - Entrypoint Principal (Dual Mode: TUI Interactivo & CLI Scriptable)
"""

import argparse
import sys

from thinkisp.errors import ThinkISPError
from thinkisp import __version__


def _handler(name):
    # --help y el parser funcionan sin consultar NM, cargar curses ni detectar hardware.
    def dispatch(args):
        from thinkisp.ui import cli
        return getattr(cli, name)(args)
    return dispatch


cmd_adblock = _handler('cmd_adblock')
cmd_band = _handler('cmd_band')
cmd_blocked = _handler('cmd_blocked')
cmd_boost = _handler('cmd_boost')
cmd_clients = _handler('cmd_clients')
cmd_dns = _handler('cmd_dns')
cmd_gaming = _handler('cmd_gaming')
cmd_kick = _handler('cmd_kick')
cmd_limit = _handler('cmd_limit')
cmd_off = _handler('cmd_off')
cmd_on = _handler('cmd_on')
cmd_qr = _handler('cmd_qr')
cmd_speed = _handler('cmd_speed')
cmd_stats = _handler('cmd_stats')
cmd_status = _handler('cmd_status')
cmd_stealth = _handler('cmd_stealth')
cmd_timer = _handler('cmd_timer')
cmd_toggle = _handler('cmd_toggle')
cmd_top = _handler('cmd_top')
cmd_unkick = _handler('cmd_unkick')
cmd_widget = _handler('cmd_widget')
cmd_setup = _handler('cmd_setup')
cmd_recover = _handler('cmd_recover')
cmd_doctor = _handler('cmd_doctor')
cmd_network = _handler('cmd_network')


def build_parser():
    """Construye el parser de argumentos de ThinkISP."""
    parser = argparse.ArgumentParser(
        prog="thinkisp",
        description="thinkISP - Configuración y gestión de un punto de acceso Linux"
    )
    parser.add_argument("--version", action="version", version=f"thinkISP {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    doctor = subparsers.add_parser('doctor', help='Diagnóstico de compatibilidad sin modificar la red')
    doctor.add_argument('--json', action='store_true', help='Informe JSON para soporte y automatización')

    network = subparsers.add_parser('network', help='Inventario y plan de conectividad por perfil (beta, sin aplicar cambios de red)')
    network.add_argument('--json', action='store_true', help='Inventario y plan en JSON')
    network_commands = network.add_subparsers(dest='network_action')
    inspect = network_commands.add_parser('inspect', help='Detectar interfaces, rutas y compatibilidad sin modificar la red')
    inspect.add_argument('--json', action='store_true', default=argparse.SUPPRESS)
    plan = network_commands.add_parser('plan', help='Preparar la selección de interfaces y motor del portal')
    plan.add_argument('--ap', help='Interfaz AP o auto')
    plan.add_argument('--upstream', help='Interfaz de entrada de Internet o auto')
    plan.add_argument('--mode', choices=['auto', 'routed', 'repeater'])
    plan.add_argument('--engine', choices=['auto', 'none', 'opennds'])
    plan.add_argument('--save', action='store_true', help='Guardar intención por perfil; no aplicar ni activar servicios')
    plan.add_argument('--json', action='store_true', default=argparse.SUPPRESS)

    # Básicos
    subparsers.add_parser("setup", aliases=["config"], help="Panel de configuración según las capacidades del equipo")
    subparsers.add_parser("on", help="Configura si es necesario y activa la zona Wi-Fi")
    subparsers.add_parser("off", help="Desactiva la zona Wi-Fi y restaura la conexión previa")
    subparsers.add_parser("boost", help="Configura CAKE y desactiva ahorro de energía Wi-Fi")
    subparsers.add_parser("status", help="Muestra el estado y dispositivos conectados")
    subparsers.add_parser("clients", help="Lista dispositivos conectados con su señal y velocidad")
    subparsers.add_parser("recover", help="Reintenta restaurar DNS y conexión anterior con el hotspot apagado")

    # 1 a 4: Telemetría & Monitoreo
    subparsers.add_parser("speed", help="Test de velocidad real (Ping, Descarga y Subida)")
    subparsers.add_parser("top", help="Monitor interactivo de tráfico en vivo por dispositivo")
    subparsers.add_parser("stats", help="Contador de datos transferidos en la sesión")
    widget_parser = subparsers.add_parser("widget", help="Salida JSON para Waybar / QuickShell")
    widget_parser.add_argument("-w", "--watch", action="store_true", help="Emitir actualizaciones continuas cada 2s")

    # 5 a 8: Seguridad & Control de Acceso
    kick_parser = subparsers.add_parser("kick", help="Bloquea el acceso de una MAC al router e Internet")
    kick_parser.add_argument("target", help="IP o dirección MAC a bloquear")
    unkick_parser = subparsers.add_parser("unkick", help="Retira el bloqueo de acceso de una MAC")
    unkick_parser.add_argument("target", help="Dirección MAC a desbloquear")
    subparsers.add_parser("blocked", help="Lista las direcciones MAC bloqueadas")
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
    dns_parser.add_argument("provider", nargs="?", default=None, help="Proveedor ('cloudflare', 'google', 'quad9', 'adguard', 'restore', 'auto', 'benchmark')")

    # Extras
    subparsers.add_parser("qr", help="Muestra el código QR para conectar móviles")
    subparsers.add_parser("toggle", help="Alterna entre encendido y apagado")
    subparsers.add_parser("tui", help="Abre el Dashboard Interactivo en terminal")
    band_parser = subparsers.add_parser("band", help="Cambia entre 5GHz y 2.4GHz")
    band_parser.add_argument("band", choices=["5g", "2.4g", "5", "2.4", "auto"], help="Banda a configurar (5g, 2.4g, auto)")

    return parser


def _main():
    parser = build_parser()
    args = parser.parse_args()

    # Si se invoca sin argumentos o con 'tui', lanzar el Dashboard Interactivo
    if not args.command or args.command == "tui":
        try:
            from thinkisp.ui.tui import launch_tui
            launch_tui()
            return
        except ImportError as exc:
            raise ThinkISPError("El panel necesita el módulo curses de Python. Instálalo con el gestor de paquetes de tu distribución o usa los comandos CLI.") from exc

    if args.command == 'network':
        cmd_network(args)
    elif args.command == 'doctor':
        cmd_doctor(args)
    elif args.command in ("setup", "config"):
        cmd_setup(args)
    elif args.command == "recover":
        cmd_recover(args)
    elif args.command == "on":
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


def main():
    if not sys.platform.startswith('linux'):
        print('thinkISP: se necesita Linux con NetworkManager; este sistema no está soportado.', file=sys.stderr)
        return 1
    if sys.version_info < (3, 9):
        print('thinkISP: se necesita Python 3.9 o posterior.', file=sys.stderr)
        return 1
    try:
        _main()
        return 0
    except (ThinkISPError, OSError) as exc:
        print(f"thinkISP: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("thinkISP: operación cancelada.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
