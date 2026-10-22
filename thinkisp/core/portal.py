"""Per-profile portal configuration and recoverable supervisor lifecycle."""
import copy
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

from thinkisp import config
from thinkisp.errors import ThinkISPError
from thinkisp.core.network import load_state, save_state, state_lock, run_cmd
from thinkisp.core.profile import profile_uuid, profile_values
from thinkisp.core.deployment import load_intent
from thinkisp.portal.broker import send_json, recv_json


def settings(owner):
    records = load_state().get('portal_profiles', {})
    if not isinstance(records, dict):
        raise ThinkISPError('La configuración del portal está dañada.')
    value = records.get(owner, {'schema_version': 1, 'enabled': False})
    if not isinstance(value, dict) or set(value) != {'schema_version', 'enabled'} or value['schema_version'] != 1 or type(value['enabled']) is not bool:
        raise ThinkISPError('Configuración del portal no compatible.')
    return dict(value)


def journal():
    value = load_state().get('portal_runtime', {})
    if not isinstance(value, dict) or (value and (value.get('schema_version') != 1 or
            value.get('phase') not in ('preparing', 'prepared', 'running', 'recovering', 'idle'))):
        raise ThinkISPError('El registro de recuperación del portal está dañado.')
    return copy.deepcopy(value)


def _record(value):
    with state_lock() as state:
        state['portal_runtime'] = copy.deepcopy(value)
        save_state(state)


def _inactive(owner):
    active = run_cmd(['nmcli', '-g', 'UUID', 'connection', 'show', '--active'], check=True)
    if owner in active.splitlines():
        raise ThinkISPError('Apaga la zona antes de cambiar o retirar el portal.')


def configure(owner, enabled):
    if type(enabled) is not bool:
        raise ThinkISPError('El estado del portal debe ser habilitado o desactivado.')
    try:
        uuid.UUID(owner)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ThinkISPError('Configura primero un perfil AP.') from exc
    from thinkisp.core.connectivity import operation_lock
    with operation_lock():
        _inactive(owner)
        settings(owner)
        if journal().get('phase', 'idle') != 'idle':
            raise ThinkISPError('Recupera primero el portal pendiente.')
        role = profile_values(['connection.type', '802-11-wireless.mode'], owner, check=True)
        if role != {'connection.type': '802-11-wireless', '802-11-wireless.mode': 'ap'}:
            raise ThinkISPError('El perfil seleccionado no es un AP.')
        intent = load_intent(owner)
        with state_lock() as state:
            if state.get('connectivity_runtime', {}).get('phase', 'idle') != 'idle':
                raise ThinkISPError('Apaga o recupera primero la conectividad.')
            network = state.get('network_profiles', {}).get(owner)
            if enabled and network is None:
                raise ThinkISPError('Guarda primero un plan en Red → Conectividad.')
            if enabled:
                values = intent.to_dict()
                values['portal_engine'] = 'builtin'
                network['intent'] = values
                network['applied'] = False
            records = state.setdefault('portal_profiles', {})
            if not isinstance(records, dict):
                raise ThinkISPError('La configuración del portal está dañada.')
            records[owner] = {'schema_version': 1, 'enabled': enabled}
            save_state(state)
    return settings(owner)


def preflight(owner):
    current = journal()
    if current.get('phase', 'idle') != 'idle' and current.get('owner') != owner:
        raise ThinkISPError('Hay una recuperación del portal de otro perfil pendiente.')
    if not settings(owner)['enabled']:
        if current.get('phase', 'idle') != 'idle':
            raise ThinkISPError('Recupera el portal pendiente antes de arrancar sin portal.')
        return
    if owner not in load_state().get('network_profiles', {}):
        raise ThinkISPError('El portal requiere un plan de conectividad guardado.')
    if load_intent(owner).portal_engine not in ('auto', 'builtin'):
        raise ThinkISPError('El portal habilitado requiere el motor Integrado; revisa Conectividad.')
    values = profile_values(['connection.autoconnect'], owner, check=True)
    if values.get('connection.autoconnect') != 'no':
        raise ThinkISPError('El portal requiere connection.autoconnect=no; desactiva la conexión automática del perfil AP antes de encenderlo.')
    missing = [name for name in ('nmcli', 'ip', 'nft') if not shutil.which(name)]
    if missing:
        raise ThinkISPError('Faltan herramientas del portal: ' + ', '.join(missing))
    if not hasattr(os, 'fork') or not hasattr(socket, 'SO_PEERCRED'):
        raise ThinkISPError('El motor de portal requiere Linux y sockets locales con credenciales.')
    if os.geteuid() != 0 and (os.environ.get('THINKISP_NO_AUTH') == '1' or not shutil.which('pkexec')):
        raise ThinkISPError('El portal requiere permisos de administración y un agente PolicyKit (pkexec).')


def _paths(value):
    try:
        instance = str(uuid.UUID(value['instance']))
        owner = str(uuid.UUID(value['owner']))
        directory = Path(value['directory'])
        expected = config.STATE_DIR.resolve() / ('portal-' + instance)
        if directory != expected or directory.is_symlink() or value.get('owner_name') != config.HOTSPOT_CONN:
            raise ValueError('directorio o perfil diferente')
        if value['owner'] != owner:
            raise ValueError('propietario inválido')
        return directory
    except (KeyError, ValueError, TypeError) as exc:
        raise ThinkISPError('La identidad de recuperación del portal es inválida.') from exc


def _rpc(value, op, payload=None):
    directory = _paths(value)
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(3)
        conn.connect(str(directory / 'control.sock'))
        from thinkisp.portal.daemon import peer_uid
        if peer_uid(conn) != 0:
            raise ThinkISPError('El socket no pertenece al supervisor privilegiado.')
        send_json(conn, {'owner': value['owner'], 'instance': value['instance'], 'op': op, 'payload': payload or {}})
        reply = recv_json(conn)
        if reply.get('ok') is not True:
            raise ThinkISPError(str(reply.get('error', 'El supervisor rechazó la solicitud.')))
        result = reply.get('result')
        if not isinstance(result, dict):
            raise ThinkISPError('Respuesta del supervisor inválida.')
        if op == 'status' and (result.get('owner') != value['owner'] or result.get('instance') != value['instance']):
            raise ThinkISPError('El supervisor corresponde a otra instancia.')
        return result


def _command(directory, recover=False):
    script = Path(__file__).resolve().parents[1] / 'portal' / 'daemon.py'
    command = [sys.executable, '-I', str(script), '--config', str(directory / 'config.json')]
    if recover:
        command.append('--recover')
    if os.geteuid() != 0:
        policykit = shutil.which('pkexec')
        if not policykit or os.environ.get('THINKISP_NO_AUTH') == '1':
            raise ThinkISPError('Reintenta la recuperación del portal desde una terminal con autorización PolicyKit.')
        command.insert(0, policykit)
    return command


def _healthy(result):
    if result.get('phase') != 'ready' or result.get('child_alive') is not True or result.get('firewall') is not True:
        raise ThinkISPError('El portal no está listo: ' + str(result.get('diagnostic') or 'falló la página o su firewall.'))


def prepare(owner, connectivity):
    if not settings(owner)['enabled']:
        return
    preflight(owner)
    if journal().get('phase', 'idle') != 'idle':
        raise ThinkISPError('Hay un portal pendiente; ejecuta thinkisp recover.')
    instance = str(uuid.uuid4())
    directory = config.STATE_DIR.resolve() / ('portal-' + instance)
    if len(os.fsencode(str(directory / 'control.sock'))) >= 108:
        raise ThinkISPError('La ruta del estado es demasiado larga para el socket del portal; elige un XDG_STATE_HOME más corto.')
    if connectivity.get('owner') != owner or connectivity.get('phase') != 'prepared':
        raise ThinkISPError('La conectividad del portal no está preparada.')
    address = ipaddress.IPv4Interface(connectivity['updates']['ipv4.addresses'])
    values = profile_values(['802-11-wireless.ssid'], owner, check=True)
    data = {'owner': owner, 'instance': instance, 'interface': connectivity['ap_interface'],
            'subnet': str(address.network), 'gateway': str(address.ip), 'caller_uid': os.getuid(),
            'label': values['802-11-wireless.ssid'] or config.PROJECT_NAME}
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    filename = directory / 'config.json'
    with os.fdopen(os.open(str(filename), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as stream:
        json.dump(data, stream)
    value = {'schema_version': 1, 'owner': owner, 'owner_name': config.HOTSPOT_CONN,
             'instance': instance, 'phase': 'preparing', 'directory': str(directory)}
    _record(value)
    process = None
    try:
        with os.fdopen(os.open(str(directory / 'daemon.log'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as log:
            process = subprocess.Popen(_command(directory), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       start_new_session=True, close_fds=True)
        end = time.monotonic() + 120
        while time.monotonic() < end:
            try:
                result = _rpc(value, 'status')
                _healthy(result)
                value.update(phase='prepared', port=result['port'], gateway=str(address.ip),
                             interface=connectivity['ap_interface'])
                _record(value)
                return value
            except (OSError, ThinkISPError):
                if process.poll() is not None:
                    detail = (directory / 'daemon.log').read_text(errors='replace')[-2000:]
                    raise ThinkISPError('No se pudo iniciar el portal: ' + detail)
                time.sleep(0.2)
        raise ThinkISPError('El portal no confirmó su arranque; autorización o recuperación pendiente.')
    except (ThinkISPError, OSError) as exc:
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                # Do not declare recovery complete while a launch may still be pending.
                value['launch_pending'] = True
                _record(value)
        raise ThinkISPError(str(exc)) from exc


def confirm(owner):
    value = journal()
    if value.get('owner') != owner or value.get('phase') != 'prepared':
        raise ThinkISPError('No hay un portal preparado para este perfil.')
    result = _rpc(value, 'status')
    _healthy(result)
    value['phase'] = 'running'
    _record(value)


def health_check(owner=None):
    value = journal()
    if not value or value.get('phase') == 'idle':
        return
    if owner is not None and value.get('owner') != owner:
        raise ThinkISPError('El portal activo pertenece a otro perfil.')
    try:
        _healthy(_rpc(value, 'status'))
    except OSError as exc:
        raise ThinkISPError('Se perdió el supervisor del portal; apaga y recupera la zona.') from exc


def cleanup(owner):
    value = journal()
    if not value or value.get('phase') == 'idle':
        return
    if value.get('owner') != owner:
        raise ThinkISPError('El portal pendiente pertenece a otro perfil.')
    _inactive(owner)
    directory = _paths(value)
    value['phase'] = 'recovering'
    _record(value)
    try:
        result = _rpc(value, 'shutdown')
        if result.get('stopped') is not True or result.get('instance') != value['instance']:
            raise ThinkISPError('El supervisor no confirmó su apagado.')
    except (ConnectionRefusedError, FileNotFoundError):
        if value.get('launch_pending'):
            raise ThinkISPError('Hay un arranque del supervisor sin confirmar; no se retiraron sus protecciones.')
        run_cmd(_command(directory, recover=True), check=True, timeout=120)
    except OSError as exc:
        raise ThinkISPError('No se pudo retirar el portal; se conservaron sus recursos.') from exc
    # No recursive privileged deletion; only remove this session's known private files.
    for name in ('config.json', 'ready.json', 'control.sock', 'daemon.log'):
        (directory / name).unlink(missing_ok=True)
    directory.rmdir()
    _record({'schema_version': 1, 'owner': owner, 'owner_name': config.HOTSPOT_CONN, 'phase': 'idle'})


def status(owner=None, check=False):
    owner = owner or profile_uuid()
    enabled = settings(owner)['enabled']
    result = {'enabled': enabled, 'engine': 'builtin' if enabled else 'none',
              'phase': 'configured' if enabled else 'disabled', 'url': None, 'clients': [], 'diagnostic': None}
    value = journal()
    if value.get('owner') == owner and value.get('phase') not in (None, 'idle'):
        result['phase'] = value['phase']
        try:
            response = _rpc(value, 'status')
            _healthy(response)
            result['clients'] = response['clients']
            if value['phase'] == 'running':
                result['url'] = f"http://{value['gateway']}:{response['port']}/"
        except (OSError, ThinkISPError) as exc:
            if check:
                raise ThinkISPError(str(exc)) from exc
            result.update(phase='recovery_pending', diagnostic=str(exc))
    return result


def _client_action(owner, target, action):
    value = journal()
    if value.get('owner') != owner or value.get('phase') != 'running':
        raise ThinkISPError('El portal de este perfil no está activo.')
    health_check(owner)
    try:
        result = _rpc(value, action, {'target': target})
    except OSError as exc:
        raise ThinkISPError('No se pudo comunicar con el portal.') from exc
    return True, result['message']


def authorize(owner, target):
    return _client_action(owner, target, 'authorize')


def revoke(owner, target):
    return _client_action(owner, target, 'revoke')
