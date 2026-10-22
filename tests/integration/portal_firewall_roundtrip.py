"""Prueba de integración en kernel para PortalFirewall en un espacio de nombres aislado."""

import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from thinkisp.core.portal_firewall import PortalFirewall, portal_table_name


def checked(args, input_text=None):
    return subprocess.run(args, input=input_text, check=True, capture_output=True, text=True).stdout


# Rechazar la ejecución en la red del equipo: se exige un user namespace
# mapeado y una red vacía con sólo loopback, antes de crear ninguna interfaz.
mapping = Path('/proc/self/uid_map').read_text().split()
interfaces = json.loads(checked(['ip', '-j', 'link', 'show']))
if (os.geteuid() != 0 or len(mapping) != 3 or mapping[0] != '0' or mapping[2] != '1'
        or [item['ifname'] for item in interfaces] != ['lo']):
    raise SystemExit('Usa: unshare --user --map-root-user --net python3 tests/integration/portal_firewall_roundtrip.py')


worker_source = r'''
import json, select, socket, subprocess, sys, threading, time
print('ready', flush=True)

streams = {}

for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    action = req.get('action')

    if action == 'run':
        res = subprocess.run(req['args'], capture_output=True, text=True)
        print(json.dumps({'code': res.returncode, 'stdout': res.stdout, 'stderr': res.stderr}), flush=True)

    elif action == 'start_tcp_server':
        port = req['port']
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('0.0.0.0', port))
        srv.listen(5)
        srv.setblocking(False)
        streams[f'srv_{port}'] = srv
        print(json.dumps({'status': 'listening', 'port': port}), flush=True)

    elif action == 'accept_connection':
        srv = streams.get(f"srv_{req['port']}")
        r, _, _ = select.select([srv], [], [], req.get('timeout', 3))
        if r:
            conn, addr = srv.accept()
            sid = req['sid']
            conn.settimeout(req.get('timeout', 3))
            streams[sid] = conn
            print(json.dumps({'status': 'accepted', 'sid': sid}), flush=True)
        else:
            print(json.dumps({'status': 'timeout'}), flush=True)

    elif action == 'http_get':
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(req.get('timeout', 2))
        try:
            s.connect((req['host'], req['port']))
            s.sendall(b"GET / HTTP/1.1\r\nHost: " + req['host'].encode() + b"\r\n\r\n")
            data = s.recv(1024)
            s.close()
            print(json.dumps({'status': 'ok', 'data': data.decode('latin1', errors='replace')}), flush=True)
        except Exception as exc:
            print(json.dumps({'status': 'error', 'detail': str(exc)}), flush=True)

    elif action == 'udp_send_recv':
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(req.get('timeout', 2))
        try:
            s.sendto(b"PING", (req['host'], req['port']))
            data, _ = s.recvfrom(1024)
            s.close()
            print(json.dumps({'status': 'ok', 'data': data.decode('latin1', errors='replace')}), flush=True)
        except Exception as exc:
            print(json.dumps({'status': 'error', 'detail': str(exc)}), flush=True)

    elif action == 'tcp_connect':
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(req.get('timeout', 2))
        try:
            s.connect((req['host'], req['port']))
            s.close()
            print(json.dumps({'status': 'connected'}), flush=True)
        except Exception as exc:
            print(json.dumps({'status': 'error', 'detail': str(exc)}), flush=True)

    elif action == 'stream_open':
        sid = req['sid']
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(req.get('timeout', 3))
        try:
            s.connect((req['host'], req['port']))
            streams[sid] = s
            print(json.dumps({'status': 'connected'}), flush=True)
        except Exception as exc:
            print(json.dumps({'status': 'error', 'detail': str(exc)}), flush=True)

    elif action == 'stream_send':
        sid = req['sid']
        s = streams.get(sid)
        try:
            s.sendall(req['data'].encode())
            print(json.dumps({'status': 'sent'}), flush=True)
        except Exception as exc:
            print(json.dumps({'status': 'error', 'detail': str(exc)}), flush=True)

    elif action == 'stream_recv':
        sid = req['sid']
        s = streams.get(sid)
        s.settimeout(req.get('timeout', 2))
        try:
            data = s.recv(1024)
            print(json.dumps({'status': 'received', 'data': data.decode()}), flush=True)
        except Exception as exc:
            print(json.dumps({'status': 'timeout' if isinstance(exc, socket.timeout) else 'error', 'detail': str(exc)}), flush=True)

    elif action == 'stream_close':
        sid = req['sid']
        if sid in streams:
            streams[sid].close()
            del streams[sid]
        print(json.dumps({'status': 'closed'}), flush=True)
'''

client = subprocess.Popen(['unshare', '--net', sys.executable, '-u', '-c', worker_source],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
upstream = subprocess.Popen(['unshare', '--net', sys.executable, '-u', '-c', worker_source],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def call_client(req):
    client.stdin.write(json.dumps(req) + '\n')
    client.stdin.flush()
    return json.loads(client.stdout.readline())


def call_upstream(req):
    upstream.stdin.write(json.dumps(req) + '\n')
    upstream.stdin.flush()
    return json.loads(upstream.stdout.readline())


try:
    assert client.stdout.readline().strip() == 'ready', 'Client namespace failed to initialize'
    assert upstream.stdout.readline().strip() == 'ready', 'Upstream namespace failed to initialize'

    # 1. Habilitar loopback e interfaces veth en router (namespace raíz de la prueba)
    checked(['ip', 'link', 'set', 'lo', 'up'])

    # Veth AP (router <-> client)
    checked(['ip', 'link', 'add', 'ap-test', 'type', 'veth', 'peer', 'name', 'client-test'])
    checked(['ip', 'link', 'set', 'client-test', 'netns', str(client.pid)])
    checked(['ip', 'link', 'set', 'ap-test', 'up'])
    checked(['ip', 'addr', 'add', '192.168.50.1/24', 'dev', 'ap-test'])
    checked(['ip', '-6', 'addr', 'add', '2001:db8:1::1/64', 'dev', 'ap-test', 'nodad'])

    # Veth WAN (router <-> upstream)
    checked(['ip', 'link', 'add', 'wan-test', 'type', 'veth', 'peer', 'name', 'wan-peer'])
    checked(['ip', 'link', 'set', 'wan-peer', 'netns', str(upstream.pid)])
    checked(['ip', 'link', 'set', 'wan-test', 'up'])
    checked(['ip', 'addr', 'add', '203.0.113.1/24', 'dev', 'wan-test'])
    checked(['ip', '-6', 'addr', 'add', '2001:db8:2::1/64', 'dev', 'wan-test', 'nodad'])

    # Habilitar reenvío IP SÓLO en este espacio de nombres privado
    Path('/proc/sys/net/ipv4/ip_forward').write_text('1\n')
    Path('/proc/sys/net/ipv6/conf/all/forwarding').write_text('1\n')

    # Configuración de interfaces en Client
    call_client({'action': 'run', 'args': ['ip', 'link', 'set', 'lo', 'up']})
    call_client({'action': 'run', 'args': ['ip', 'link', 'set', 'client-test', 'up']})
    call_client({'action': 'run', 'args': ['ip', 'addr', 'add', '192.168.50.2/24', 'dev', 'client-test']})
    call_client({'action': 'run', 'args': ['ip', '-6', 'addr', 'add', '2001:db8:1::2/64', 'dev', 'client-test', 'nodad']})
    call_client({'action': 'run', 'args': ['ip', 'route', 'add', 'default', 'via', '192.168.50.1']})
    call_client({'action': 'run', 'args': ['ip', '-6', 'route', 'add', 'default', 'via', '2001:db8:1::1']})

    res_link = call_client({'action': 'run', 'args': ['ip', '-j', 'link', 'show', 'client-test']})
    client_mac = json.loads(res_link['stdout'])[0]['address']

    # Configuración de interfaces en Upstream
    call_upstream({'action': 'run', 'args': ['ip', 'link', 'set', 'lo', 'up']})
    call_upstream({'action': 'run', 'args': ['ip', 'link', 'set', 'wan-peer', 'up']})
    call_upstream({'action': 'run', 'args': ['ip', 'addr', 'add', '203.0.113.2/24', 'dev', 'wan-peer']})
    call_upstream({'action': 'run', 'args': ['ip', '-6', 'addr', 'add', '2001:db8:2::2/64', 'dev', 'wan-peer', 'nodad']})
    call_upstream({'action': 'run', 'args': ['ip', 'route', 'add', 'default', 'via', '203.0.113.1']})
    call_upstream({'action': 'run', 'args': ['ip', '-6', 'route', 'add', 'default', 'via', '2001:db8:2::1']})

    # Tabla externa como fixture para asegurar su preservación
    checked(['nft', 'add', 'table', 'inet', 'external_fixture'])

    # Servicios del host router:
    # 1. UDP 67 (DHCP)
    udp_dhcp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_dhcp.bind(('192.168.50.1', 67))
    udp_dhcp.setblocking(False)

    # 2. UDP 53 (DNS)
    udp_dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_dns.bind(('192.168.50.1', 53))
    udp_dns.setblocking(False)

    # 3. TCP 8080 (Portal HTTP)
    tcp_portal = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_portal.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_portal.bind(('192.168.50.1', 8080))
    tcp_portal.listen(10)
    tcp_portal.setblocking(False)

    # 4. TCP 9999 (Servicio administrativo no portal: debe bloquearse)
    tcp_admin = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_admin.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_admin.bind(('192.168.50.1', 9999))
    tcp_admin.listen(5)
    tcp_admin.setblocking(False)

    # Comprobación base de conectividad
    res_base = call_client({'action': 'run', 'args': ['ping', '-c', '1', '-W', '1', '203.0.113.2']})
    assert res_base['code'] == 0, 'La conectividad de base con Upstream debe funcionar antes del firewall'

    base_v6 = call_client({'action': 'run', 'args': ['ping', '-6', '-c', '2', '-W', '2', '2001:db8:2::2']})
    assert base_v6['code'] == 0, f'La ruta IPv6 de base debe funcionar: {base_v6}'

    # Instalar PortalFirewall
    owner = "11111111-1111-1111-1111-111111111111"
    instance = "22222222-2222-2222-2222-222222222222"
    fw = PortalFirewall(
        owner=owner,
        interface="ap-test",
        subnet="192.168.50.0/24",
        gateway="192.168.50.1",
        port=8080,
        instance=instance,
    )
    fw.install()
    summary = fw.verify()
    assert summary['verified'] is True

    # Comprobar que servicios input autorizados del router responden:
    # A) UDP 67 (DHCP) permitido
    call_client({'action': 'udp_send_recv', 'host': '192.168.50.1', 'port': 67, 'timeout': 1})
    r, _, _ = select.select([udp_dhcp], [], [], 1)
    assert r, "El tráfico DHCP (UDP 67) debe ser permitido hacia el gateway"
    udp_dhcp.recvfrom(1024)

    # B) UDP 53 (DNS) permitido
    call_client({'action': 'udp_send_recv', 'host': '192.168.50.1', 'port': 53, 'timeout': 1})
    r, _, _ = select.select([udp_dns], [], [], 1)
    assert r, "El tráfico DNS (UDP 53) debe ser permitido hacia el gateway"
    udp_dns.recvfrom(1024)

    # C) TCP 8080 (Portal) permitido
    res_portal = call_client({'action': 'tcp_connect', 'host': '192.168.50.1', 'port': 8080, 'timeout': 1})
    assert res_portal['status'] == 'connected', "La conexión al puerto del portal debe permitirse"
    conn_p, _ = tcp_portal.accept()
    conn_p.close()

    # D) TCP 9999 (Servicio no-portal) DENEGADO
    res_admin = call_client({'action': 'tcp_connect', 'host': '192.168.50.1', 'port': 9999, 'timeout': 1})
    assert res_admin['status'] == 'error', "Servicios administrativos no-portal deben ser denegados"

    # E) Reenvío no autenticado DENEGADO (ping e IP a upstream)
    res_ping_unauth = call_client({'action': 'run', 'args': ['ping', '-c', '1', '-W', '1', '203.0.113.2']})
    assert res_ping_unauth['code'] != 0, "Ping a upstream debe ser bloqueado antes de autorización"

    # F) Redirección HTTP no autorizada hacia el puerto del portal
    def respond_redirect_portal():
        r, _, _ = select.select([tcp_portal], [], [], 2)
        if r:
            conn, _ = tcp_portal.accept()
            req = conn.recv(1024)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\nPORTAL")
            conn.close()

    t_redir = threading.Thread(target=respond_redirect_portal)
    t_redir.start()
    res_http = call_client({'action': 'http_get', 'host': '203.0.113.2', 'port': 80, 'timeout': 2})
    t_redir.join()
    assert res_http['status'] == 'ok' and 'PORTAL' in res_http['data'], "El tráfico HTTP dport 80 debe redirigirse al portal"

    # G) Autorización de cliente
    fw.set_authorized([{'ip': '192.168.50.2', 'mac': client_mac}])
    assert fw.verify()['authorized_count'] == 1

    # H) Tráfico reenviado permitido tras autorización
    res_ping_auth = call_client({'action': 'run', 'args': ['ping', '-c', '1', '-W', '1', '203.0.113.2']})
    assert res_ping_auth['code'] == 0, "El cliente autorizado debe poder reenviar tráfico a upstream"

    # Una conexión HTTP nueva de un cliente autorizado llega al upstream.
    call_upstream({'action': 'start_tcp_server', 'port': 80})
    assert call_client({'action': 'tcp_connect', 'host': '203.0.113.2', 'port': 80})['status'] == 'connected'
    assert call_upstream({'action': 'accept_connection', 'sid': 'http_authorized', 'port': 80})['status'] == 'accepted'
    call_upstream({'action': 'stream_close', 'sid': 'http_authorized'})

    # I) Flujo TCP establecido y corte inmediato por revocación
    call_upstream({'action': 'start_tcp_server', 'port': 12345})
    # Conectar stream desde client
    res_client_conn = call_client({'action': 'stream_open', 'sid': 'client_stream', 'host': '203.0.113.2', 'port': 12345})
    assert res_client_conn['status'] == 'connected'
    # Aceptar en upstream
    res_srv_accept = call_upstream({'action': 'accept_connection', 'sid': 'srv_stream', 'port': 12345})
    assert res_srv_accept['status'] == 'accepted'

    # Verificar que el stream funciona antes de revocación
    call_client({'action': 'stream_send', 'sid': 'client_stream', 'data': 'PING_BEFORE'})
    res_srv_data = call_upstream({'action': 'stream_recv', 'sid': 'srv_stream', 'timeout': 1})
    assert res_srv_data['status'] == 'received' and res_srv_data['data'] == 'PING_BEFORE'

    # Revocar autorización vaciando los conjuntos
    fw.set_authorized([])
    assert fw.verify()['authorized_count'] == 0

    # Comprobar que el ping queda bloqueado de inmediato
    assert call_client({'action': 'run', 'args': ['ping', '-c', '1', '-W', '1', '203.0.113.2']})['code'] != 0

    # Comprobar que el stream activo queda bloqueado inmediatamente (prioridad -150)
    call_client({'action': 'stream_send', 'sid': 'client_stream', 'data': 'DROP_AFTER'})
    res_srv_after = call_upstream({'action': 'stream_recv', 'sid': 'srv_stream', 'timeout': 1})
    assert res_srv_after['status'] == 'timeout', "El stream revocado no debe poder transmitir datos (debe expirar)"

    call_client({'action': 'stream_close', 'sid': 'client_stream'})
    call_upstream({'action': 'stream_close', 'sid': 'srv_stream'})

    # J) IPv6 bloqueado en ambas direcciones
    res_v6 = call_client({'action': 'run', 'args': ['ping', '-6', '-c', '1', '-W', '1', '2001:db8:2::2']})
    assert res_v6['code'] != 0, "IPv6 reenviado debe ser bloqueado"

    # Las autorizaciones expiran en el kernel si el supervisor deja de refrescarlas.
    fw.set_authorized([{'ip': '192.168.50.2', 'mac': client_mac}])
    assert call_client({'action': 'run', 'args': ['ping', '-c', '1', '-W', '1', '203.0.113.2']})['code'] == 0
    time.sleep(16)
    assert fw.verify()['authorized_count'] == 0
    assert call_client({'action': 'run', 'args': ['ping', '-c', '1', '-W', '1', '203.0.113.2']})['code'] != 0

    # K) Preservación de tabla externa
    checked(['nft', 'list', 'table', 'inet', 'external_fixture'])

    # L) Limpieza idempotente
    fw.remove()
    tables_after = json.loads(checked(['nft', '-j', 'list', 'tables']))['nftables']
    assert not any(t.get('table', {}).get('name') == fw.table for t in tables_after), "Tabla propia debe eliminarse"
    # Idempotencia: segunda llamada sin error
    fw.remove()
    # Confirmar que external_fixture sigue existiendo
    checked(['nft', 'list', 'table', 'inet', 'external_fixture'])

    print("PASS: portal_firewall_roundtrip verificado con éxito en el kernel.")

finally:
    client.terminate()
    upstream.terminate()
    client.wait(timeout=3)
    upstream.wait(timeout=3)
