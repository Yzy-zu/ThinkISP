"""Privileged local supervisor; HTTP requests run in a separate unprivileged child."""
import argparse
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import stat
import struct
import sys
import threading
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from thinkisp.errors import ThinkISPError
from thinkisp.core.network import run_cmd
from thinkisp.core.portal_firewall import PortalFirewall
from thinkisp.portal.broker import PortalBroker, recv_json, send_json
from thinkisp.portal.http import make_server


def read_config(filename):
    path = Path(filename)
    if not path.is_absolute() or path.is_symlink() or path.parent.is_symlink():
        raise ThinkISPError('Ruta privada del portal inválida.')
    directory = path.parent.stat()
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or stat.S_IMODE(directory.st_mode) != 0o700:
            raise ThinkISPError('Permisos de configuración del portal inválidos.')
        with os.fdopen(fd, 'r') as stream:
            fd = None
            data = json.loads(stream.read(16385))
    finally:
        if fd is not None:
            os.close(fd)
    expected = {'owner', 'instance', 'interface', 'subnet', 'gateway', 'caller_uid', 'label'}
    if not isinstance(data, dict) or set(data) != expected:
        raise ThinkISPError('Configuración del portal no compatible.')
    uid = data['caller_uid']
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        raise ThinkISPError('Propietario del portal inválido.')
    caller = int(os.environ.get('PKEXEC_UID', os.getuid()))
    if caller != uid or info.st_uid != uid or directory.st_uid != uid:
        raise ThinkISPError('La configuración no pertenece a quien inició el portal.')
    # Validate all network fields using the same firewall boundary before any mutation.
    PortalFirewall(data['owner'], data['interface'], data['subnet'], data['gateway'], 1024, data['instance'])
    if path.parent.name != 'portal-' + data['instance'] or path.name != 'config.json':
        raise ThinkISPError('Identidad del directorio del portal inválida.')
    if not isinstance(data['label'], str) or len(data['label'].encode()) > 256:
        raise ThinkISPError('Nombre del portal inválido.')
    return path, data


def inactive(owner):
    active = run_cmd(['nmcli', '-g', 'UUID', 'connection', 'show', '--active'], check=True, timeout=5)
    if owner in active.splitlines():
        raise ThinkISPError('Apaga el AP antes de retirar el portal.')


def credentials(uid):
    try:
        entry = pwd.getpwuid(uid) if uid else pwd.getpwnam('nobody')
    except KeyError as exc:
        raise ThinkISPError('No se encontró una cuenta sin privilegios para la página HTTP.') from exc
    if entry.pw_uid == 0:
        raise ThinkISPError('La página del portal requiere una cuenta sin privilegios.')
    return entry.pw_uid, entry.pw_gid


def peer_uid(conn):
    return struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


def write_ready(path, data, port):
    ready = {'owner': data['owner'], 'instance': data['instance'], 'port': port}
    fd = os.open(str(path.parent / 'ready.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(ready, stream)
        stream.flush()
        os.fchown(stream.fileno(), data['caller_uid'], -1)


def recover(path, data):
    inactive(data['owner'])
    sock_path = path.parent / 'control.sock'
    if sock_path.exists():
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(2)
            try:
                conn.connect(str(sock_path))
            except (ConnectionRefusedError, FileNotFoundError):
                pass
            else:
                raise ThinkISPError('Hay un supervisor activo; usa su apagado normal.')
    ready_path = path.parent / 'ready.json'
    if not ready_path.exists():
        return  # Ready metadata is written before the first firewall mutation.
    if ready_path.is_symlink():
        raise ThinkISPError('Metadatos del portal inválidos.')
    ready = json.loads(ready_path.read_text())
    if ready.get('owner') != data['owner'] or ready.get('instance') != data['instance']:
        raise ThinkISPError('Los recursos pendientes pertenecen a otra instancia.')
    PortalFirewall(data['owner'], data['interface'], data['subnet'], data['gateway'],
                   ready['port'], data['instance']).remove()


def http_child(listener, channel, uid, gid, data, parent):
    # No admin listener, root control channel, or main state file is inherited.
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    lock = threading.Lock()
    channel.settimeout(5)

    def backend(op, payload):
        with lock:
            try:
                send_json(channel, {'op': op, 'payload': payload})
                reply = recv_json(channel)
            except (OSError, ThinkISPError) as exc:
                raise ThinkISPError('Supervisor no disponible.') from exc
            if reply.get('ok') is not True:
                raise ThinkISPError(str(reply.get('error', 'Solicitud rechazada.')))
            return reply['result']

    def watchdog():
        while os.getppid() == parent:
            time.sleep(0.25)
        os._exit(1)

    threading.Thread(target=watchdog, daemon=True).start()
    server = make_server(listener, data['gateway'], listener.getsockname()[1], data['label'], backend)
    send_json(channel, {'ready': True})
    server.serve_forever()


def stop_child(pid):
    if pid is None:
        return
    try:
        if os.waitpid(pid, os.WNOHANG)[0]:
            return
        os.kill(pid, signal.SIGTERM)
        end = time.monotonic() + 2
        while time.monotonic() < end:
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass  # Already reaped: never signal a potentially reused PID.


def serve(path, data):
    uid, gid = credentials(data['caller_uid'])
    interface = data['interface']
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b'\0')
    listener.bind(('0.0.0.0', 0))
    listener.listen(32)
    port = listener.getsockname()[1]
    if port < 1024:
        raise ThinkISPError('No se obtuvo un puerto HTTP sin privilegios.')
    write_ready(path, data, port)
    fw = PortalFirewall(data['owner'], interface, data['subnet'], data['gateway'], port, data['instance'])
    fw.install()  # Empty guard exists before any ready response or AP activation.
    broker = PortalBroker(fw)
    admin = socket.socket(socket.AF_UNIX)
    admin.bind(str(path.parent / 'control.sock'))
    os.chmod(str(path.parent / 'control.sock'), 0o600)
    os.chown(str(path.parent / 'control.sock'), data['caller_uid'], -1)
    admin.listen(8)
    admin.settimeout(0.2)
    parent_channel, child_channel = socket.socketpair()
    parent = os.getpid()
    child = os.fork()  # Fork before starting any threads.
    if child == 0:
        admin.close()
        parent_channel.close()
        try:
            http_child(listener, child_channel, uid, gid, data, parent)
        except BaseException:
            os._exit(1)
        os._exit(0)
    child_channel.close()
    listener.close()
    parent_channel.settimeout(5)
    stop = threading.Event()

    def on_signal(*args):
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        if recv_json(parent_channel) != {'ready': True}:
            raise ThinkISPError('La página HTTP no confirmó su arranque.')

        def child_requests():
            while not stop.is_set():
                try:
                    request = recv_json(parent_channel)
                    if set(request) != {'op', 'payload'}:
                        raise ThinkISPError('Solicitud inválida del proceso HTTP.')
                    try:
                        result = broker.child_call(request['op'], request['payload'])
                        reply = {'ok': True, 'result': result}
                    except ThinkISPError as exc:
                        reply = {'ok': False, 'error': str(exc)}
                    send_json(parent_channel, reply)
                except socket.timeout:
                    continue
                except (OSError, ThinkISPError, KeyError, TypeError) as exc:
                    broker.fail('Se perdió el proceso HTTP: ' + str(exc))
                    break

        def refresh():
            while not stop.wait(3):
                try:
                    broker.refresh()
                except ThinkISPError:
                    break

        threading.Thread(target=child_requests, daemon=True).start()
        threading.Thread(target=refresh, daemon=True).start()
        while not stop.is_set():
            if child is not None and os.waitpid(child, os.WNOHANG)[0]:
                child = None
                broker.fail('La página HTTP se detuvo.')
            try:
                conn, _ = admin.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(3)
                try:
                    if peer_uid(conn) not in (0, data['caller_uid']):
                        raise ThinkISPError('Acceso administrativo denegado.')
                    req = recv_json(conn)
                    if set(req) != {'owner', 'instance', 'op', 'payload'} or req['owner'] != data['owner'] or req['instance'] != data['instance']:
                        raise ThinkISPError('La instancia del portal no coincide.')
                    op, payload = req['op'], req['payload']
                    if op == 'status' and payload == {}:
                        result = dict(broker.status(), owner=data['owner'], instance=data['instance'],
                                      port=port, child_alive=child is not None)
                    elif op in ('authorize', 'revoke') and isinstance(payload, dict) and set(payload) == {'target'}:
                        message = getattr(broker, op)(payload['target'])
                        result = {'message': message}
                    elif op == 'shutdown' and payload == {}:
                        inactive(data['owner'])
                        broker.fail('Apagado solicitado.')
                        stop_child(child)
                        child = None
                        fw.remove()
                        result = {'stopped': True, 'owner': data['owner'], 'instance': data['instance']}
                        stop.set()
                    else:
                        raise ThinkISPError('Operación administrativa inválida.')
                    send_json(conn, {'ok': True, 'result': result})
                except (ThinkISPError, OSError, ValueError, KeyError, TypeError) as exc:
                    try:
                        send_json(conn, {'ok': False, 'error': str(exc)})
                    except OSError:
                        pass
    finally:
        stop.set()
        broker.fail('El supervisor se detuvo.')
        stop_child(child)
        parent_channel.close()
        admin.close()
        # Guard remains on signal/crash. Only verified inactive shutdown/recover removes it.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--recover', action='store_true')
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise ThinkISPError('El supervisor del portal requiere permisos de administración.')
        path, data = read_config(args.config)
        recover(path, data) if args.recover else serve(path, data)
        return 0
    except (ThinkISPError, OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
