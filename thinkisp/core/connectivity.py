"""Recoverable execution of a saved connectivity plan; portal remains disabled."""
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shutil
import uuid
import time
from collections import Counter

from thinkisp import config
from thinkisp.core.network import CommandError, ThinkISPError, load_state, run_cmd, save_state, state_lock
from thinkisp.core.profile import profile_values, settings_arguments, _offline_template
from thinkisp.core.deployment import load_intent
from thinkisp.core.topology import collect_inventory, evaluate_plan, NetworkIntent


PROFILE_FIELDS = ('connection.interface-name', '802-11-wireless.band', '802-11-wireless.channel',
                  'ipv4.addresses', 'ipv6.method', 'connection.autoconnect')


@contextmanager
def operation_lock():
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (config.STATE_DIR / 'connectivity.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ThinkISPError('Ya hay una operación de conectividad en curso.') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def journal():
    value = load_state().get('connectivity_runtime', {})
    if not isinstance(value, dict):
        raise ThinkISPError('Registro de recuperación de conectividad dañado.')
    if value and (value.get('schema_version') != 1 or value.get('phase') not in
                  ('idle', 'preparing', 'prepared', 'running', 'recovering')):
        raise ThinkISPError('Versión o fase del registro de conectividad no compatible.')
    return copy.deepcopy(value)


def record(value):
    with state_lock() as state:
        state['connectivity_runtime'] = copy.deepcopy(value)
        save_state(state)


def has_intent(state, owner):
    records = state.get('network_profiles', {})
    if not isinstance(records, dict):
        raise ThinkISPError('La configuración de conectividad está dañada.')
    return owner in records


def privileged(args):
    if args[0] not in ('ip', 'iw'):
        raise ThinkISPError('Comando de conectividad no permitido.')
    binary = shutil.which(args[0])
    if not binary:
        raise ThinkISPError(f'Falta {args[0]} para aplicar conectividad.')
    command = [binary, *args[1:]]
    try:
        return run_cmd(command, check=True, timeout=30)
    except CommandError as exc:
        if os.geteuid() and any(word in exc.detail.lower() for word in ('operation not permitted', 'permission denied', 'must be root')):
            pkexec = shutil.which('pkexec')
            if pkexec and os.environ.get('THINKISP_NO_AUTH') != '1':
                return run_cmd([pkexec, *command], check=True, timeout=120)
            raise ThinkISPError('Crear interfaces/rutas necesita CAP_NET_ADMIN o autorización PolicyKit.') from exc
        raise


def virtual_name(owner, existing):
    for index in range(128):
        name = 'ti' + hashlib.sha256(f'{os.getuid()}:{owner}:{index}'.encode()).hexdigest()[:11]
        if name not in existing:
            return name
    raise ThinkISPError('No se encontró un nombre libre para el AP virtual.')


def select_subnet(owner, configured, occupied):
    networks = [ipaddress.ip_network(value, strict=False) for value in occupied]
    networks = [value for value in networks if value.version == 4 and value.prefixlen]
    if configured:
        values = configured.split(',')
        if len(values) != 1:
            raise ThinkISPError('La conectividad requiere una sola dirección/subred IPv4 compartida.')
        try:
            address = ipaddress.ip_interface(values[0].strip())
        except ValueError as exc:
            raise ThinkISPError('La subred IPv4 configurada no es válida.') from exc
        if address.version != 4 or address.network.num_addresses < 4 or address.ip in (address.network.network_address, address.network.broadcast_address):
            raise ThinkISPError('Elige una dirección IPv4 de host con espacio para clientes DHCP.')
        if any(address.network.overlaps(other) for other in networks):
            raise ThinkISPError('La subred del AP se solapa con una red existente. Cámbiala en Configurar.')
        return str(address)
    seed = int(hashlib.sha256(owner.encode()).hexdigest()[:8], 16)
    for pool in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'):
        parent = ipaddress.ip_network(pool)
        size = 2 ** (24 - parent.prefixlen)
        for offset in range(min(size, 1024)):
            network = ipaddress.ip_network((int(parent.network_address) + ((seed + offset) % size) * 256, 24))
            if not any(network.overlaps(other) for other in networks):
                return str(ipaddress.ip_interface(f'{network.network_address + 1}/24'))
    raise ThinkISPError('No se encontró una subred privada libre para el AP.')


def route_resources(owner, ap, upstream, subnet, gateway, upstream_prefix, rules, routes, reserved=()):
    used = {int(item['priority']) for item in rules if 'priority' in item}
    tables = {str(item.get('table', 'main')) for item in rules + routes}
    seed = int(hashlib.sha256(f'{os.getuid()}:{owner}'.encode()).hexdigest()[:8], 16)
    tables.update(str(item) for item in reserved)
    boundary = min((int(item['priority']) for item in rules
                    if str(item.get('table')) not in ('local', '255') and 'priority' in item), default=32766)
    if boundary <= 1:
        raise ThinkISPError('Las reglas existentes no dejan prioridad segura para el hotspot.')
    priority = next((p for p in range(1, boundary) if p not in used), None)
    table = next((30000 + (seed + i) % 30000 for i in range(30000)
                  if str(30000 + (seed + i) % 30000) not in tables), None)
    if priority is None or table is None:
        raise ThinkISPError('No hay tabla/prioridad libre para las rutas del hotspot.')
    additions = []
    if upstream_prefix:
        additions.append(['ip', '-4', 'route', 'add', 'table', str(table), upstream_prefix, 'dev', upstream, 'scope', 'link'])
    additions.append(['ip', '-4', 'route', 'add', 'table', str(table), 'unreachable', subnet])
    default = ['ip', '-4', 'route', 'add', 'table', str(table), 'default']
    if gateway:
        default += ['via', str(ipaddress.IPv4Address(gateway))]
    default += ['dev', upstream]
    additions.append(default)
    additions.append(['ip', '-4', 'route', 'add', 'table', str(table), 'unreachable', 'default', 'metric', '32767'])
    # Applies only to packets arriving from the selected AP subnet/interface.
    additions.append(['ip', '-4', 'rule', 'add', 'priority', str(priority), 'from', subnet, 'iif', ap, 'lookup', str(table)])
    return {'table': table, 'priority': priority, 'resources': [{'add': cmd, 'delete': ['del' if part == 'add' else part for part in cmd]}
                                                               for cmd in additions]}


def _current_uuid(interface):
    value = run_cmd(['nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', interface], check=True)
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _prepare(owner):
    """Validate every input before persisting an intent-to-mutate journal."""
    previous = journal()
    if previous and previous.get('phase') != 'idle':
        raise ThinkISPError('Hay conectividad pendiente o activa; apaga o ejecuta thinkisp recover primero.')
    inventory = collect_inventory()
    intent = load_intent(owner)
    plan = evaluate_plan(inventory, intent, fallback_ap=config.WLAN_IFACE)
    if not plan['configuration_valid']:
        raise ThinkISPError('; '.join(item['detail'] for item in plan['issues'] if item['level'] == 'error'))
    selected = plan['selected']
    physical, upstream = selected['ap_interface'], selected['upstream_interface']
    devices = {item['name']: item for item in inventory['devices']}
    if devices[physical].get('managed') is not True:
        raise ThinkISPError('No se confirmó gestión de NetworkManager en el adaptador AP.')
    upstream_uuid = _current_uuid(upstream)
    if upstream_uuid is None:
        raise ThinkISPError('La salida elegida no tiene una conexión activa de NetworkManager.')
    properties = profile_values(list(PROFILE_FIELDS), owner, check=True)
    role = profile_values(['connection.type','802-11-wireless.mode','ipv4.method'], owner, check=True)
    if role != {'connection.type':'802-11-wireless','802-11-wireless.mode':'ap','ipv4.method':'shared'}:
        raise ThinkISPError('El perfil no es un AP con IPv4 compartido.')
    original = dict(properties)
    updates = {'connection.interface-name': physical, 'ipv6.method':'disabled','connection.autoconnect':'no'}
    create_virtual = selected['mode'] == 'repeater' and physical == upstream
    ap = virtual_name(owner, devices) if create_virtual else physical
    phy = devices[physical].get('phy')
    client_channel = None
    if selected['mode'] == 'repeater':
        if plan['concurrency']['status'] != 'advertised' or plan['concurrency']['same_channel_required'] is None:
            raise ThinkISPError('No se confirmó concurrencia cliente/AP y su presupuesto de canales.')
        counts = Counter(d.get('wireless_mode') or ('managed' if d['name'] == upstream else 'AP')
                         for d in inventory['devices'] if d.get('phy') == phy)
        if create_virtual:
            counts['AP'] += 1
        else:
            previous_mode = devices[physical].get('wireless_mode') or 'AP'
            counts[previous_mode] -= 1
            counts['AP'] += 1
        combinations = inventory['radios'][phy].get('combinations', [])
        fitting = [c for c in combinations if c.get('managed_ap') and sum(counts.values()) <= c['max_interfaces'] and
                   all(sum(counts[m] for m in g['modes']) <= g['limit'] for g in c.get('limits', []))]
        if not fitting:
            raise ThinkISPError('Las interfaces existentes agotan el presupuesto de concurrencia de la radio.')
        info = run_cmd(['iw','dev',upstream,'info'], check=True)
        if not re.search(r'\btype managed\b',info):
            raise ThinkISPError('La entrada Wi-Fi no está en modo cliente.')
        channel = re.search(r'channel (\d+) \((\d+(?:\.\d+)?) MHz\)',info)
        if not channel:
            raise ThinkISPError('No se pudo confirmar el canal del enlace cliente.')
        if all(c['max_channels'] == 1 for c in fitting):
            client_channel = int(channel[1])
            number, frequency = int(channel[1]), float(channel[2])
            band = 'bg' if 2400 <= frequency < 2500 else 'a' if 4900 <= frequency < 5925 else '6GHz'
            details = inventory['radios'][phy].get('ap_channel_details', [])
            if not any(c['frequency'] == frequency and c['number'] == number for c in details):
                raise ThinkISPError('El canal de la entrada Wi-Fi no está autorizado para AP en esta radio.')
            fixed_band, fixed_channel = properties.get('802-11-wireless.band',''), properties.get('802-11-wireless.channel') or '0'
            if (fixed_band and fixed_band != band) or (fixed_channel != '0' and int(fixed_channel) != number):
                raise ThinkISPError('Una radio requiere el canal/banda del cliente. Selecciona banda y canal automáticos o coincidentes.')
            updates.update({'802-11-wireless.band':band,'802-11-wireless.channel':str(number)})
    updates['connection.interface-name'] = ap
    addresses = query_json(['ip','-j','-4','address','show'])
    routes = query_json(['ip','-j','-4','route','show','table','all'])
    rules = query_json(['ip','-j','-4','rule','show'])
    occupied = [f"{a['local']}/{a['prefixlen']}" for dev in addresses if dev['ifname'] != ap
                for a in dev.get('addr_info',[]) if a.get('family') == 'inet']
    occupied += [r['dst'] for r in routes if r.get('dst') not in (None,'default') and r.get('dev') != ap
                 and r.get('table','main') not in ('local',255) and ipaddress.ip_network(r['dst'],strict=False).prefixlen >= 8]
    subnet_address = select_subnet(owner,properties.get('ipv4.addresses',''),occupied)
    subnet = str(ipaddress.ip_interface(subnet_address).network)
    updates['ipv4.addresses'] = subnet_address
    dhcp_range = profile_values(['ipv4.shared-dhcp-range'], owner).get('ipv4.shared-dhcp-range', '')
    if dhcp_range:
        validate_dhcp_range(dhcp_range, subnet_address)
    upstream_addresses = [ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}") for dev in addresses if dev['ifname'] == upstream
                          for a in dev.get('addr_info',[]) if a.get('family') == 'inet']
    defaults = []
    for route in routes:
        if route.get('dst') == 'default' and route.get('table','main') in ('main',254) and route.get('type','unicast') == 'unicast':
            for hop in ([route] if route.get('dev') else route.get('nexthops',[])):
                if hop.get('dev') == upstream and not set(hop.get('flags',[])) & {'dead','linkdown'}:
                    defaults.append((route.get('metric',0),hop.get('gateway')))
    if not defaults or not upstream_addresses:
        raise ThinkISPError('La salida necesita dirección IPv4 y ruta predeterminada; sólo IPv6 todavía no está soportado.')
    gateway = min(defaults,key=lambda item:item[0])[1]
    if gateway:
        matching = [a for a in upstream_addresses if ipaddress.ip_address(gateway) in a.network]
        if not matching:
            raise ThinkISPError('La pasarela de la salida no pertenece a una subred conectada verificable.')
        upstream_prefix = str(matching[0].network)
    else:
        upstream_prefix = None
    resources = route_resources(owner,ap,upstream,subnet,gateway,upstream_prefix,rules,routes, reserved_tables())
    run_cmd(['nmcli','--offline','connection','modify',*settings_arguments(updates)],input_text=_offline_template(),check=True)
    # Mark every owned resource before the first mutation; interrupted calls are recoverable.
    value = {'schema_version':1,'owner':owner,'owner_name':config.HOTSPOT_CONN,'phase':'preparing',
             'ap_interface':ap,'physical_interface':physical,'upstream_interface':upstream,'mode':selected['mode'],
             'upstream_uuid':upstream_uuid,'client_channel':client_channel,'phy':phy,'create_virtual':create_virtual,'virtual_attempted':False,'virtual_ifindex':None,
             'original':original,'updates':updates,'profile_attempted':False,'routing':resources,
             'attempted_resources':0,'subnet':subnet,'gateway':gateway,'upstream_prefix':upstream_prefix,
             'previous_ap_uuid':_current_uuid(physical) if not create_virtual else None}
    record(value)
    try:
        if create_virtual:
            value['virtual_attempted']=True
            record(value)
            try:
                privileged(['iw','phy',phy,'interface','add',ap,'type','__ap'])
            except CommandError as exc:
                if exc.returncode is not None:
                    value['virtual_attempted'] = False
                    record(value)
                raise
            link = query_json(['ip','-j','link','show','dev',ap])
            value['virtual_ifindex']=link[0]['ifindex']
            record(value)
            for attempt in range(16):
                try:
                    run_cmd(['nmcli','device','set',ap,'managed','yes'],check=True,timeout=3)
                    break
                except CommandError:
                    if attempt == 15:
                        raise
                    time.sleep(0.25)
        value['profile_attempted']=True
        record(value)
        run_cmd(['nmcli','connection','modify','--temporary','uuid',owner,*settings_arguments(updates)],check=True)
        for index, resource in enumerate(resources['resources']):
            value['attempted_resources']=index+1
            record(value)
            try:
                privileged(resource['add'])
            except CommandError as exc:
                if exc.returncode is not None:
                    value['attempted_resources'] = index
                    record(value)
                raise
        value['phase']='prepared'
        record(value)
    except (ThinkISPError,ValueError,KeyError,TypeError,IndexError,OSError) as exc:
        try:
            cleanup(owner)
        except ThinkISPError as recovery:
            raise ThinkISPError(f'{exc}. Recuperación de conectividad pendiente: {recovery}') from exc
        raise ThinkISPError(str(exc)) from exc
    return value


def confirm(owner):
    value = journal()
    if value.get('owner') != owner or value.get('phase') != 'prepared':
        raise ThinkISPError('No existe un arranque de conectividad preparado para este perfil.')
    selected_route = query_json(['ip','-j','-4','route','get','203.0.113.1','from',
                       str(ipaddress.ip_network(value['subnet']).network_address+2),'iif',value['ap_interface']])
    if not selected_route or selected_route[0].get('dev') != value['upstream_interface']:
        raise ThinkISPError('El tráfico de clientes no está encaminado por la salida elegida.')
    health_check(value)
    value['phase']='running'
    record(value)
    with state_lock() as state:
        if owner in state.get('network_profiles',{}):
            state['network_profiles'][owner]['applied']=True
        save_state(state)
    return value


def cleanup(owner):
    """Remove only journal-owned resources; preserve state when recovery fails."""
    value=journal()
    if not value or value.get('phase') == 'idle':
        return
    if value.get('owner') != owner:
        raise ThinkISPError('La conectividad pendiente pertenece a otro perfil.')
    active = run_cmd(['nmcli','-g','UUID','connection','show','--active'],check=True).splitlines()
    if owner in active:
        raise ThinkISPError('Apaga el AP antes de retirar sus rutas o interfaz virtual.')
    value['phase']='recovering'
    record(value)
    # Restore temporary properties only if no external actor has changed them.
    if value.get('profile_attempted'):
        current=profile_values(list(value['updates']),owner,check=True)
        def comparable(key,v):
            if key.endswith('.channel'):
                return v or '0'
            return v
        for key, desired in value['updates'].items():
            if comparable(key,current.get(key,'')) not in (comparable(key,str(desired)),comparable(key,value['original'].get(key,''))):
                raise ThinkISPError(f'{key} cambió externamente; se conservó el registro de recuperación.')
        run_cmd(['nmcli','connection','modify','--temporary','uuid',owner,*settings_arguments(value['original'])],check=True)
        value['profile_attempted']=False
        record(value)
    for index in reversed(range(value.get('attempted_resources',0))):
        resource=value['routing']['resources'][index]
        command=resource['delete']
        try:
            if owned_resource(resource, value['routing']):
                privileged(command)
        except CommandError as exc:
            if not any(text in exc.detail.lower() for text in ('no such file','cannot find','no such process')):
                raise
        value['attempted_resources']=index
        record(value)
    if value.get('virtual_attempted'):
        links=query_json(['ip','-j','link','show'])
        link=next((item for item in links if item['ifname']==value['ap_interface']),None)
        if link:
            if value.get('virtual_ifindex') is None or link['ifindex'] != value['virtual_ifindex']:
                raise ThinkISPError('La interfaz virtual no tiene identidad verificable; no se eliminó.')
            info=run_cmd(['iw','dev',value['ap_interface'],'info'],check=True)
            if not re.search(r'^\s*wiphy ' + re.escape(value['phy'][3:]) + r'\s*$', info, re.M):
                raise ThinkISPError('La interfaz virtual cambió de radio; no se eliminó.')
            privileged(['iw','dev',value['ap_interface'],'del'])
        value['virtual_attempted']=False
        record(value)
    previous=value.get('previous_ap_uuid')
    if previous:
        current=_current_uuid(value['physical_interface'])
        if current is None:
            run_cmd(['nmcli','--wait','45','connection','up','uuid',previous,'ifname',value['physical_interface']],check=True,timeout=50)
        elif current != previous:
            raise ThinkISPError('Otra conexión está usando el adaptador AP; no se sustituyó.')
    idle={'schema_version':1,'owner':owner,'owner_name':value['owner_name'],'phase':'idle',
          'ap_interface':value['physical_interface']}
    record(idle)
    with state_lock() as state:
        if owner in state.get('network_profiles',{}):
            state['network_profiles'][owner]['applied']=False
        if state.get('session', {}).get('profile_uuid') == owner:
            state.pop('session', None)
        state.pop('last_wifi', None)
        save_state(state)


def reserved_tables():
    from pathlib import Path
    result = {0, 253, 254, 255}
    for root in ('/etc/iproute2', '/usr/lib/iproute2'):
        paths = [Path(root) / 'rt_tables', *sorted((Path(root) / 'rt_tables.d').glob('*.conf'))]
        for path in paths:
            try:
                for line in path.read_text().splitlines():
                    fields = line.split('#', 1)[0].split()
                    if fields and fields[0].isdigit():
                        result.add(int(fields[0]))
            except FileNotFoundError:
                pass
    return result


def health_check(value=None):
    value = value if value is not None else journal()
    if value.get('phase') not in ('prepared', 'running'):
        return
    if _current_uuid(value['upstream_interface']) != value['upstream_uuid']:
        raise ThinkISPError('Cambió o se perdió la conexión de entrada; apaga y vuelve a iniciar el AP.')
    if value.get('client_channel'):
        info = run_cmd(['iw', 'dev', value['upstream_interface'], 'info'], check=True)
        match = re.search(r'channel (\d+) ', info)
        if not match or int(match[1]) != value['client_channel']:
            raise ThinkISPError('La entrada cambió de canal; hay que reiniciar el repetidor.')


def owned_resource(resource, routing):
    command = resource['add']
    if command[:4] not in (['ip','-4','rule','add'], ['ip','-4','route','add']):
        raise ThinkISPError('Registro de rutas dañado.')
    if resource['delete'] != ['del' if item == 'add' else item for item in command]:
        raise ThinkISPError('Registro de borrado de rutas dañado.')
    def argument(name, default=None):
        return command[command.index(name)+1] if name in command else default
    if command[2] == 'rule':
        rows = query_json(['ip','-j','-4','rule','show'])
        rows = [r for r in rows if r.get('priority') == routing['priority']]
        if not rows:
            return False
        source = rows[0].get('src', '')
        if '/' not in source and 'srclen' in rows[0]:
            source += '/' + str(rows[0]['srclen'])
        if len(rows) != 1 or str(rows[0].get('table')) != str(routing['table']) or source != argument('from') or rows[0].get('iif') != argument('iif'):
            raise ThinkISPError('La regla cambió externamente; no se eliminó.')
    else:
        rows = query_json(['ip','-j','-4','route','show','table','all'])
        rows = [r for r in rows if str(r.get('table','main')) == str(routing['table'])]
        kind = 'unreachable' if 'unreachable' in command else 'unicast'
        dest = command[7] if kind == 'unreachable' else command[6]
        rows = [r for r in rows if r.get('dst','default') == dest and r.get('type','unicast') == kind and r.get('metric',0) == int(argument('metric',0))]
        if not rows:
            return False
        if len(rows) != 1 or rows[0].get('dev') != argument('dev') or rows[0].get('gateway') != argument('via'):
            raise ThinkISPError('La ruta cambió externamente; no se eliminó.')
    return True


def prepare(owner):
    try:
        return _prepare(owner)
    except (ValueError, KeyError, TypeError, IndexError, OSError) as exc:
        raise ThinkISPError(f'No se pudo interpretar la conectividad del sistema: {exc}') from exc


def query_json(command):
    try:
        result = json.loads(run_cmd(command, check=True))
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise ValueError('se esperaba una lista de objetos')
        return result
    except (ValueError, TypeError) as exc:
        raise ThinkISPError(f'La respuesta de {command[0]} no es válida: {exc}') from exc


def validate_dhcp_range(value, subnet_address):
    try:
        start, end = (ipaddress.IPv4Address(part.strip()) for part in value.split(','))
        address = ipaddress.ip_interface(subnet_address)
        if (start > end or start not in address.network or end not in address.network or
                start == address.network.network_address or end == address.network.broadcast_address or
                start <= address.ip <= end):
            raise ValueError('rango fuera de la subred o incluye al router')
    except ValueError as exc:
        raise ThinkISPError('El rango DHCP no corresponde a la subred del AP; revisa Configurar antes de encender.') from exc
