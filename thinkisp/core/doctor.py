"""Diagnóstico de compatibilidad: consultas y validación offline, sin cambios de red."""

import importlib
import json
import locale
import os
import platform
import shutil
import sys

from thinkisp import config
from thinkisp.core.network import ThinkISPError, load_state, run_cmd
from thinkisp.core.profile import _offline_template, inspect_capabilities, probe_property


TOOLS = {
    'nmcli': (True, 'Gestionar el AP con NetworkManager'),
    'iw': (True, 'Consultar y configurar la radio Wi-Fi'),
    'ip': (True, 'Consultar interfaces, rutas y vecinos'),
    'nft': (False, 'Bloquear el acceso de clientes'),
    'tc': (False, 'Comprobar las colas y límites de tráfico'),
    'qrencode': (False, 'Mostrar códigos QR'),
    'dig': (False, 'Comparar proveedores DNS'),
    'curl': (False, 'Medir transferencia'),
    'ethtool': (False, 'Leer velocidad del enlace de salida'),
    'notify-send': (False, 'Notificaciones del escritorio'),
    'pkexec': (False, 'Autorización interactiva para nftables'),
}


def collect_report():
    checks = []

    def add(key, status, detail, required=False):
        checks.append({'id': key, 'status': status, 'required': required, 'detail': detail})

    add('linux', 'ok' if sys.platform.startswith('linux') else 'error', platform.platform(), True)
    add('python', 'ok' if sys.version_info >= (3, 9) else 'error', platform.python_version(), True)
    binaries = {}
    for name, (required, feature) in TOOLS.items():
        binaries[name] = shutil.which(name)
        add(name, 'ok' if binaries[name] else ('error' if required else 'warning'),
            f'{feature}: {binaries[name]}' if binaries[name] else f'{feature}: falta {name}. Instálalo si vas a usar esta función.', required)
    try:
        importlib.import_module('curses')
        add('curses', 'ok', 'Módulo Python disponible para el panel.')
    except ImportError:
        add('curses', 'warning', 'El CLI funciona; el panel necesita el módulo curses de la distribución.')
    term = os.environ.get('TERM', '')
    encoding = locale.nl_langinfo(locale.CODESET)
    interactive = (sys.stdin.isatty() and sys.stdout.isatty() and term not in ('', 'dumb')
                   and encoding.lower().replace('-', '') == 'utf8')
    add('terminal', 'ok' if interactive else 'warning',
        f'TERM={term or "sin definir"}; locale={encoding}. ' + ('Terminal interactiva.' if interactive else 'Sin terminal UTF-8 interactiva compatible; usa los comandos CLI.'))

    template = None
    daemon_available = False
    if binaries['nmcli']:
        version = run_cmd(['nmcli', '--version']) or 'Versión no disponible'
        add('nmcli_version', 'ok', version)
        try:
            template = _offline_template()
            if 'mode=ap' not in template or 'method=shared' not in template:
                raise ThinkISPError('El parser no generó un perfil AP compartido.')
            add('nmcli_offline', 'ok', 'Perfil AP validado sin crearlo. --offline requiere NetworkManager 1.40 o posterior.', True)
        except ThinkISPError as exc:
            add('nmcli_offline', 'error', f'{exc}. Se necesita nmcli con --offline (1.40+).', True)
        try:
            status = run_cmd(['nmcli', '-t', '-f', 'STATE', 'general'], check=True)
            daemon_available = True
            add('networkmanager_service', 'ok', f'Servicio accesible: {status}.', True)
            permissions = run_cmd(['nmcli', '-t', 'general', 'permissions'])
            if permissions:
                relevant = [line for line in permissions.splitlines()
                            if line.startswith(('org.freedesktop.NetworkManager.network-control:',
                                                'org.freedesktop.NetworkManager.settings.modify.system:'))]
                allowed = relevant and all(line.rsplit(':', 1)[-1] == 'yes' for line in relevant)
                add('networkmanager_permissions', 'ok' if allowed else 'warning',
                    '; '.join(relevant) or 'Permisos sin confirmar; el sistema puede pedir autorización.')
            radio = run_cmd(['nmcli', '-t', '-f', 'WIFI-HW,WIFI', 'general'])
            if radio:
                enabled = all(value == 'enabled' for value in radio.split(':'))
                blocked = 'disabled' in radio.split(':')
                add('wifi_enabled', 'ok' if enabled else ('error' if blocked else 'warning'),
                    f'Radio hardware/software: {radio}. No se cambia su estado.', required=blocked or enabled)
        except ThinkISPError as exc:
            add('networkmanager_service', 'error', f'No se pudo consultar NetworkManager: {exc}', True)
    features = {}
    if template:
        for key, value in {
            '802-11-wireless.ap-isolation': '1',
            '802-11-wireless-security.pmf': '2',
            'ipv4.shared-dhcp-range': '',
            'ipv4.shared-dhcp-lease-time': '0',
            '802-11-wireless.channel-width': 'auto',
            '802-11-wireless-security.key-mgmt': 'sae',
        }.items():
            features[key] = probe_property(template, key, value)
        add('nmcli_optional_properties', 'ok', 'Disponibilidad del parser consultada; no confirma el soporte del controlador.')

    hardware = {'interface': config.ap_interface(config.WLAN_IFACE), 'activation_tested': False}
    if not config.ap_interface(config.WLAN_IFACE):
        add('wifi_ap', 'error', 'No se detectó Wi-Fi. Comprueba el adaptador y el controlador, o selecciona THINKISP_WLAN.', True)
    elif daemon_available and binaries['iw'] and template:
        managed = run_cmd(['nmcli', '-g', 'GENERAL.NM-MANAGED', 'device', 'show', config.ap_interface(config.WLAN_IFACE)])
        if managed == 'no':
            add('wifi_managed', 'error', f'{config.ap_interface(config.WLAN_IFACE)} no está gestionada por NetworkManager.', True)
        try:
            caps = inspect_capabilities(allow_unusable=True)
            hardware.update(driver=caps.driver, phy=caps.phy, bands=caps.bands,
                            widths=caps.widths, widths_by_band=caps.widths_by_band,
                            security=caps.security, security_notes=caps.security_notes,
                            channels=[{'band': c.band, 'channel': c.number, 'frequency_mhz': c.frequency,
                                       'max_dbm': c.max_dbm, 'restrictions': c.flags}
                                      for c in caps.channels if c.usable and c.band in caps.bands],
                            reported_bands=sorted({c.band for c in caps.channels if c.band}),
                            unparsed_frequency_lines=[line.strip() for line in caps.raw.splitlines()
                                                      if 'MHz' in line and '[' in line][:8] if not caps.channels else [],
                            restricted_channels=[{'band': c.band, 'channel': c.number, 'restrictions': c.flags}
                                                 for c in caps.channels if not c.usable], regulatory=caps.regulatory)
            if caps.bands:
                add('wifi_ap', 'ok', f'{caps.interface}: AP anunciado; bandas aceptadas por radio y nmcli: {", ".join(caps.bands)}.', True)
            else:
                radio_usable = any(c.usable and c.band for c in caps.channels)
                reason = ('No se pudo interpretar la lista de canales de iw.' if not caps.channels else
                          ('nmcli no aceptó las bandas disponibles en la radio.' if radio_usable else
                           'No hay canales autorizados para iniciar un AP; revisa las restricciones regulatorias del controlador.'))
                add('wifi_ap', 'error', f'{caps.interface}: {reason}', True)
        except ThinkISPError as exc:
            add('wifi_ap', 'error', str(exc), True)
    else:
        add('wifi_ap', 'unverified', 'No se pudieron consultar las capacidades AP con las herramientas y permisos disponibles.', True)

    upstream = config.upstream_interface(config.detect_eth_iface(config.ap_interface(config.WLAN_IFACE)))
    add('upstream', 'ok' if upstream and upstream != config.ap_interface(config.WLAN_IFACE) else 'warning',
        f'Salida detectada: {upstream}.' if upstream and upstream != config.ap_interface(config.WLAN_IFACE) else 'Sin salida distinta del AP; compartir Internet y cambiar DNS requiere una.')
    dnsmasq = shutil.which('dnsmasq')
    add('shared_dhcp_dns', 'ok' if dnsmasq else 'warning',
        'dnsmasq disponible; no se inicia ni se comprueba DHCP.' if dnsmasq else 'No se encontró dnsmasq en PATH. El modo compartido requiere el backend DHCP/DNS que use NetworkManager.')
    if binaries['nft']:
        try:
            data = json.loads(run_cmd(['nft', '-j', 'list', 'tables'], check=True))
            if not isinstance(data.get('nftables'), list):
                raise ValueError('Respuesta JSON sin lista nftables')
            add('nft_permissions', 'ok', 'Lectura JSON disponible. El hook netdev/ingress necesita validación en el kernel; no se modificaron reglas.')
        except (ThinkISPError, ValueError, AttributeError) as exc:
            add('nft_permissions', 'warning', f'Lectura nftables sin confirmar: {exc}. El bloqueo necesita CAP_NET_ADMIN o autorización PolicyKit.')
    try:
        load_state()
        add('state_read', 'ok', 'Estado legible o todavía no creado; no se escribe ningún archivo.')
    except ThinkISPError as exc:
        add('state_read', 'error', str(exc), True)
    add('activation', 'unverified', 'No se activó el AP ni se probaron asociación, DHCP, navegación o compatibilidad de los clientes.')
    failed = [check['id'] for check in checks if check['required'] and check['status'] != 'ok']
    return {'schema_version': 1, 'system': {'os': sys.platform, 'architecture': platform.machine(),
             'kernel': platform.release(), 'python': platform.python_version()},
            'core_checks_passed': not failed, 'failed_required_checks': failed,
            'checks': checks, 'nmcli_features': features, 'wifi': hardware, 'upstream': upstream}
