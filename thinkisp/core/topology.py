"""Read-only inventory and topology planning. No interface or firewall mutations."""
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import shutil

from thinkisp.core.network import ThinkISPError, run_cmd
from thinkisp.core.profile import parse_channels
from thinkisp.parsing import split_nmcli


@dataclass(frozen=True)
class NetworkIntent:
    ap_interface: str = None
    upstream_interface: str = None
    mode: str = 'auto'
    portal_engine: str = 'auto'

    def validate(self):
        if not isinstance(self.mode, str) or self.mode not in ('auto', 'routed', 'repeater'):
            raise ThinkISPError('Modo de red inválido: usa auto, routed o repeater.')
        if not isinstance(self.portal_engine, str) or self.portal_engine not in ('auto', 'none', 'opennds'):
            raise ThinkISPError('Motor inválido: usa auto, none u opennds.')
        for value in (self.ap_interface, self.upstream_interface):
            if value is not None and (not isinstance(value, str) or not value or len(value.encode()) > 15
                                      or re.search(r'[\s/:\x00-\x1f\x7f]', value)):
                raise ThinkISPError('El nombre de interfaz no es válido.')
        return self

    def to_dict(self):
        return asdict(self)


def interface_combinations(raw):
    """Parse iw groups rather than assuming AP+managed implies concurrency."""
    entries, current, indent = [], '', None
    for line in raw.expandtabs(8).splitlines():
        if 'valid interface combinations:' in line:
            indent = len(line) - len(line.lstrip())
            continue
        if indent is None or not line.strip():
            continue
        depth = len(line) - len(line.lstrip())
        if depth <= indent:
            break
        if line.lstrip().startswith('*'):
            if current:
                entries.append(current)
            current = line.strip()
        else:
            current += ' ' + line.strip()
    if current:
        entries.append(current)
    result = []
    for entry in entries:
        groups = [(set(name.strip() for name in names.split(',')), int(limit))
                  for names, limit in re.findall(r'#\{([^}]+)\}\s*<=\s*(\d+)', entry)]
        total = re.search(r'\btotal\s*<=\s*(\d+)', entry)
        channels = re.search(r'#channels\s*<=\s*(\d+)', entry)
        required = {'managed', 'AP'}
        allows = bool(groups and total and int(total[1]) >= 2
                      and required <= set().union(*(names for names, _ in groups))
                      and all(len(names & required) <= limit for names, limit in groups))
        result.append({'managed_ap': allows, 'max_interfaces': int(total[1]) if total else None,
                       'max_channels': int(channels[1]) if channels else None})
    return result


def parse_wireless_devices(raw):
    result, phy, interface = {}, None, None
    for line in raw.splitlines():
        match = re.match(r'\s*phy#(\d+)', line)
        if match:
            phy = 'phy' + match[1]
        match = re.match(r'\s*Interface\s+(.+)', line)
        if match:
            interface = match[1].strip()
            result[interface] = {'phy': phy, 'wireless_mode': None}
        match = re.match(r'\s*type\s+(\S+)', line)
        if match and interface:
            result[interface]['wireless_mode'] = match[1]
    return result


def parse_routes(raw, family):
    records = []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError('Se esperaba una lista de rutas')
    for route in data:
        if route.get('type', 'unicast') != 'unicast' or any(flag in route.get('flags', []) for flag in ('dead', 'linkdown')):
            continue
        for hop in ([route] if route.get('dev') else route.get('nexthops', [])):
            if hop.get('dev') and not any(flag in hop.get('flags', []) for flag in ('dead', 'linkdown')):
                records.append({'interface': hop['dev'], 'family': family, 'metric': int(route.get('metric', 0))})
    return records


def _device_bus(interface):
    path = Path('/sys/class/net') / interface / 'device'
    if path.exists():
        return 'usb' if any(re.fullmatch(r'usb\d+', part) for part in path.resolve().parts) else 'other'
    return 'unknown'


def collect_inventory():
    """Missing permissions/tools remain unverified, never an invented capability."""
    errors = []
    def query(args):
        try:
            return run_cmd(args, check=True)
        except ThinkISPError as exc:
            errors.append({'tool': args[0], 'detail': str(exc)})
            return None
    devices = {}
    nm = query(['nmcli', '-t', '-f', 'DEVICE,TYPE,STATE', 'device'])
    if nm is not None:
        for line in nm.splitlines():
            parts = split_nmcli(line)
            if len(parts) == 3 and parts[0] != 'lo':
                name, kind, state = parts
                devices[name] = {'name': name, 'kind': kind, 'state': state, 'managed': None,
                                 'bus': _device_bus(name), 'phy': None}
    links = query(['ip', '-j', 'link', 'show'])
    if links is not None:
        try:
            records = json.loads(links)
            if not isinstance(records, list):
                raise ValueError('Se esperaba una lista de interfaces')
            for link in records:
                name = link['ifname']
                if name != 'lo':
                    devices.setdefault(name, {'name': name, 'kind': 'unknown', 'state': 'unverified',
                                             'managed': None, 'bus': _device_bus(name), 'phy': None})
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({'tool': 'ip', 'detail': f'No se pudo interpretar inventario: {exc}'})
    wireless = parse_wireless_devices(query(['iw', 'dev']) or '')
    radios = {}
    for name, radio in wireless.items():
        device = devices.setdefault(name, {'name': name, 'kind': 'wifi', 'state': 'unverified',
                                          'managed': None, 'bus': _device_bus(name)})
        device.update(radio)
        device['kind'] = 'wifi'
    for device in devices.values():
        if device['kind'] == 'wifi':
            managed = query(['nmcli', '-g', 'GENERAL.NM-MANAGED', 'device', 'show', device['name']])
            device['managed'] = True if managed == 'yes' else False if managed == 'no' else None
            phy = device.get('phy')
            if phy and phy not in radios:
                raw = query(['iw', 'phy', phy, 'info'])
                mode_block = re.search(r'Supported interface modes:\s*\n((?:\s*\*[^\n]+\n?)+)', raw or '')
                modes = re.findall(r'\*\s*(\S+)', mode_block[1]) if mode_block else []
                radios[phy] = {'verified': raw is not None, 'modes': modes,
                               'combinations': interface_combinations(raw or ''),
                               'ap_channels': [c.number for c in parse_channels(raw or '') if c.usable and c.band]}
    routes = []
    for family in (4, 6):
        raw = query(['ip', '-j', '-' + str(family), 'route', 'show', 'default'])
        if raw is not None:
            try:
                routes.extend(parse_routes(raw, family))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                errors.append({'tool': 'ip', 'detail': f'Rutas IPv{family} sin confirmar: {exc}'})
    # Inventory only: discovering a binary is not proof its runtime is integrated.
    engines = {'none': {'installed': True, 'runtime_implemented': True},
               'opennds': {'installed': bool(shutil.which('opennds')),
                           'control_installed': bool(shutil.which('ndsctl')), 'runtime_implemented': False}}
    dependencies = {name: bool(shutil.which(name)) for name in ('nmcli', 'iw', 'ip', 'nft', 'dnsmasq', 'ss')}
    tables = query(['nft', '-j', 'list', 'tables']) if dependencies['nft'] else None
    firewall = {'status': 'unverified', 'table_count': None}
    if tables is not None:
        try:
            entries = json.loads(tables)['nftables']
            firewall = {'status': 'readable', 'table_count': sum('table' in item for item in entries)}
        except (ValueError, KeyError, TypeError):
            errors.append({'tool': 'nft', 'detail': 'Inventario de tablas sin confirmar.'})
    return {'schema_version': 1, 'devices': sorted(devices.values(), key=lambda d: d['name']),
            'radios': radios, 'routes': routes, 'dependencies': dependencies,
            'engines': engines, 'firewall': firewall, 'errors': errors, 'network_changed': False}


def evaluate_plan(inventory, intent, fallback_ap=None):
    intent.validate()
    issues = []
    def issue(code, level, detail):
        issues.append({'code': code, 'level': level, 'detail': detail})
    devices = {d['name']: d for d in inventory['devices']}
    ap_name = intent.ap_interface or fallback_ap
    if not ap_name:
        ap_name = next((d['name'] for d in devices.values() if d.get('kind') == 'wifi'
                        and d.get('managed') is not False
                        and 'AP' in inventory['radios'].get(d.get('phy'), {}).get('modes', [])), None)
    ap = devices.get(ap_name)
    if ap is None:
        issue('ap_missing', 'error', 'No se encontró el adaptador AP seleccionado; elige uno del inventario.')
    elif ap.get('kind') != 'wifi':
        issue('ap_not_wifi', 'error', 'La interfaz AP seleccionada no es Wi-Fi.')
    else:
        radio = inventory['radios'].get(ap.get('phy'), {})
        if ap.get('managed') is False:
            issue('ap_unmanaged', 'error', 'NetworkManager no gestiona el adaptador AP.')
        elif ap.get('managed') is None:
            issue('ap_management_unknown', 'warning', 'Gestión de NetworkManager sin confirmar.')
        if not radio.get('verified') or not radio.get('modes'):
            issue('ap_unknown', 'warning', 'Capacidades AP sin confirmar; faltan datos o permisos.')
        elif 'AP' not in radio['modes']:
            issue('ap_unsupported', 'error', 'El controlador no anuncia modo AP.')
        elif not radio.get('ap_channels'):
            issue('ap_channels_unavailable', 'error', 'La radio no anuncia canales utilizables para iniciar AP.')
    routes = sorted(inventory['routes'], key=lambda r: (r['family'] != 4, r['metric']))
    upstream_name = intent.upstream_interface
    if not upstream_name:
        upstream_name = next((r['interface'] for r in routes if r['interface'] in devices), None)
    upstream = devices.get(upstream_name)
    if upstream is None:
        issue('upstream_missing', 'error', 'No se detectó una salida; conecta Ethernet, USB o Wi-Fi y vuelve a consultar.')
    elif not any(r['interface'] == upstream_name for r in routes):
        issue('upstream_no_route', 'warning', 'La salida seleccionada no tiene ruta predeterminada; Internet sin confirmar.')
    elif not any(r['interface'] == upstream_name and r['family'] == 4 for r in routes):
        issue('upstream_ipv6_only', 'warning', 'Salida sólo IPv6: no confirma servicio IPv4 compartido.')
    same_radio = bool(ap and upstream and ap.get('phy') and ap.get('phy') == upstream.get('phy'))
    if ap_name and ap_name == upstream_name:
        same_radio = bool(ap and ap.get('kind') == 'wifi')
    mode = ('repeater' if same_radio else 'routed') if intent.mode == 'auto' else intent.mode
    concurrency = {'status': 'not_required', 'same_channel_required': None, 'activation_tested': False}
    if (ap and upstream and ap_name != upstream_name and ap.get('kind') == upstream.get('kind') == 'wifi'
            and (not ap.get('phy') or not upstream.get('phy'))):
        concurrency['status'] = 'unverified'
        issue('radio_identity_unknown', 'warning', 'No se pudo comprobar si las interfaces Wi-Fi comparten radio; topología provisional.')
    if mode == 'routed' and ap_name and ap_name == upstream_name:
        issue('same_interface', 'error', 'AP y salida no pueden usar la misma interfaz en modo routed.')
    if mode == 'routed' and same_radio:
        issue('shared_radio', 'error', 'Son interfaces distintas de la misma radio: requieren modo repeater y concurrencia.')
    if mode == 'repeater':
        if not same_radio:
            issue('different_radios', 'error', 'Para radios separadas usa routed; repeater describe una radio compartida.')
        radio = inventory['radios'].get(ap.get('phy'), {}) if ap else {}
        combinations = radio.get('combinations', [])
        valid = [c for c in combinations if c['managed_ap']]
        concurrency['status'] = 'advertised' if valid else 'unsupported' if combinations else 'unverified'
        if valid and all(c.get('max_channels') is not None for c in valid):
            concurrency['same_channel_required'] = all(c['max_channels'] == 1 for c in valid)
        if not valid:
            issue('concurrency', 'error' if combinations else 'warning',
                  'El controlador no anuncia concurrencia cliente + AP.' if combinations else 'Concurrencia cliente + AP sin confirmar.')
        elif concurrency['same_channel_required']:
            issue('same_channel_required', 'warning', 'Cliente y AP deben seguir el mismo canal según el controlador.')
        issue('repeater_runtime_pending', 'warning', 'Plan guardable; crear y gestionar las interfaces virtuales corresponde a fase 2.')
    engine = intent.portal_engine
    if engine == 'auto':
        engine = 'opennds' if inventory['engines'].get('opennds', {}).get('installed') else 'none'
    if engine == 'opennds':
        if not inventory['engines'].get('opennds', {}).get('installed'):
            issue('portal_dependency_missing', 'warning', 'openNDS no está instalado; será una dependencia opcional del portal.')
        if not inventory['dependencies'].get('nft'):
            issue('portal_firewall_missing', 'warning', 'Falta nftables para preparar el control de acceso del portal.')
        if not inventory['engines'].get('opennds', {}).get('control_installed'):
            issue('portal_control_missing', 'warning', 'No se encontró ndsctl; control de openNDS sin confirmar.')
        issue('portal_runtime_pending', 'warning', 'Motor identificado; su integración y autorización de clientes corresponden a fase 3.')
    if inventory['firewall']['status'] == 'unverified':
        issue('firewall_unverified', 'warning', 'No se pudo consultar nftables; compatibilidad de reglas sin verificar.')
    elif inventory['firewall']['table_count']:
        issue('firewall_existing', 'warning', 'Hay tablas nftables existentes; deben conservarse y comprobarse al integrar el portal.')
    return {'schema_version': 1, 'phase': 1, 'intent': intent.to_dict(),
            'selected': {'ap_interface': ap_name, 'upstream_interface': upstream_name, 'mode': mode, 'portal_engine': engine},
            'concurrency': concurrency, 'issues': issues, 'configuration_valid': not any(i['level'] == 'error' for i in issues),
            'activation_tested': False, 'portal_enabled': False, 'applied': False, 'internet_verified': False}
