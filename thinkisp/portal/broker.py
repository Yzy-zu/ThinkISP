"""Authorization state and bounded local IPC; never persists client grants."""
import ipaddress
import json
import secrets
import threading
import time

from thinkisp.core.access import normalize_mac
from thinkisp.core.network import ThinkISPError, run_cmd

MAX_FRAME = 65536


def send_json(sock, value):
    data = json.dumps(value, ensure_ascii=True).encode() + b'\n'
    if len(data) > MAX_FRAME:
        raise ThinkISPError('Mensaje del portal demasiado grande.')
    sock.sendall(data)


def recv_json(sock):
    # One request per admin connection; the child serializes calls over a socketpair.
    data = bytearray()
    while True:
        part = sock.recv(1)
        if not part:
            raise ThinkISPError('El supervisor cerró la conexión.')
        if part == b'\n':
            break
        data.extend(part)
        if len(data) >= MAX_FRAME:
            raise ThinkISPError('Mensaje del portal demasiado grande.')
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError('se esperaba un objeto')
        return value
    except (ValueError, UnicodeDecodeError) as exc:
        raise ThinkISPError('Respuesta del portal inválida.') from exc


class PortalBroker:
    def __init__(self, firewall, neighbors=None, clock=time.monotonic):
        self.firewall = firewall
        self.subnet = firewall.subnet
        self.gateway = firewall.gateway
        self.neighbors = neighbors or self._neighbors
        self.clock = clock
        self.lock = threading.RLock()
        self.grants = {}
        self.nonces = {}
        self.fault = None

    def _neighbors(self):
        try:
            entries = json.loads(run_cmd(
                ['ip', '-j', '-4', 'neigh', 'show', 'dev', self.firewall.interface],
                check=True, timeout=3))
            if not isinstance(entries, list):
                raise ValueError('se esperaba una lista')
            result = {}
            for item in entries:
                if not isinstance(item, dict):
                    raise ValueError('vecino inválido')
                states = item.get('state', [])
                if isinstance(states, str):
                    states = [states]
                if not item.get('lladdr') or set(states) & {'FAILED', 'INCOMPLETE'}:
                    continue
                parsed = ipaddress.IPv4Address(item['dst'])
                if parsed not in self.subnet or parsed in (self.gateway, self.subnet.network_address, self.subnet.broadcast_address):
                    continue
                ip = str(parsed)
                mac = normalize_mac(item['lladdr'])
                if ip in result and result[ip] != mac:
                    raise ThinkISPError('La IP del cliente tiene varias MAC posibles.')
                result[ip] = mac
            return result
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ThinkISPError('No se pudo verificar la identidad del cliente.') from exc

    def client_ip(self, value):
        try:
            if not isinstance(value, str):
                raise ValueError()
            ip = ipaddress.IPv4Address(value)
            if ip not in self.subnet or ip in (
                    self.gateway, self.subnet.network_address, self.subnet.broadcast_address):
                raise ValueError()
            return str(ip)
        except ValueError as exc:
            raise ThinkISPError('La dirección no pertenece a los clientes del portal.') from exc

    def _available(self):
        if self.fault:
            raise ThinkISPError('Motor del portal no disponible: ' + self.fault)

    def _apply(self, desired):
        self._available()
        try:
            self.firewall.set_authorized([{'ip': ip, 'mac': mac} for ip, mac in sorted(desired.items())])
        except ThinkISPError as exc:
            self.fail(str(exc))
            raise
        self.grants = dict(desired)

    def fail(self, reason):
        with self.lock:
            self.fault = reason
            self.grants.clear()
            self.nonces.clear()
            try:
                self.firewall.set_authorized([])
            except ThinkISPError:
                pass  # Kernel health leases still expire; guard is retained.

    def _resolve(self, target):
        if not isinstance(target, str):
            raise ThinkISPError('Indica la IP o MAC de un cliente.')
        target = target.strip()
        neighbors = self.neighbors()
        try:
            ipaddress.IPv4Address(target)
        except ValueError:
            mac = normalize_mac(target)
            matches = [ip for ip, value in neighbors.items() if value == mac]
            if len(matches) != 1:
                raise ThinkISPError('No hay una IP inequívoca para esa MAC en el AP.')
            ip = self.client_ip(matches[0])
        else:
            ip = self.client_ip(target)
            if ip not in neighbors:
                raise ThinkISPError('No se encontró ese cliente en la interfaz AP.')
            mac = normalize_mac(neighbors[ip])
        return ip, mac

    def _grant(self, ip, mac):
        desired = {i: m for i, m in self.grants.items() if i != ip and m != mac}
        desired[ip] = mac
        self._apply(desired)

    def child_call(self, op, payload):
        fields = {'ip', 'token'} if op == 'accept' else {'ip'}
        if op not in ('client_status', 'challenge', 'accept') or not isinstance(payload, dict) or set(payload) != fields:
            raise ThinkISPError('Solicitud del portal inválida.')
        ip = self.client_ip(payload['ip'])
        with self.lock:
            self._available()
            current = self.neighbors()
            if op == 'client_status':
                authorized = ip in self.grants and current.get(ip) == self.grants[ip]
                return {'authorized': authorized}
            if op == 'challenge':
                if ip not in current:
                    raise ThinkISPError('No se confirmó la MAC del cliente.')
                now = self.clock()
                self.nonces = {token: value for token, value in self.nonces.items() if now - value[2] < 120}
                while len(self.nonces) >= 1024:
                    self.nonces.pop(next(iter(self.nonces)))
                token = secrets.token_urlsafe(32)
                self.nonces[token] = (ip, normalize_mac(current[ip]), now)
                return {'token': token}
            token = payload['token']
            if not isinstance(token, str) or not 1 <= len(token) <= 128:
                raise ThinkISPError('Token inválido.')
            challenge = self.nonces.pop(token, None)
            if not challenge or self.clock() - challenge[2] >= 120 or challenge[0] != ip or current.get(ip) != challenge[1]:
                raise ThinkISPError('Token inválido, vencido o identidad del cliente modificada.')
            self._grant(ip, challenge[1])
            return {'authorized': True}

    def authorize(self, target):
        with self.lock:
            self._available()
            ip, mac = self._resolve(target)
            self._grant(ip, mac)
            return f'{ip} ({mac}): acceso autorizado.'

    def revoke(self, target):
        with self.lock:
            self._available()
            if not isinstance(target, str):
                raise ThinkISPError('Indica la IP o MAC del cliente.')
            target = target.strip().lower()
            desired = {ip: mac for ip, mac in self.grants.items() if target not in (ip, mac)}
            if desired == self.grants:
                raise ThinkISPError('El cliente no tiene una autorización activa.')
            self._apply(desired)
            # Revoke old challenges too: a new visit must request a new nonce.
            self.nonces.clear()
            return f'{target}: autorización revocada; puede volver a entrar al portal.'

    def refresh(self):
        with self.lock:
            if self.fault:
                return
            try:
                current = self.neighbors()
                self._apply({ip: mac for ip, mac in self.grants.items() if current.get(ip) == mac})
            except ThinkISPError as exc:
                self.fail(str(exc))
                raise

    def status(self):
        with self.lock:
            summary = self.firewall.verify()
            return {'phase': 'fault' if self.fault else 'ready', 'diagnostic': self.fault,
                    'clients': [{'ip': ip, 'mac': mac, 'authorized': True}
                                for ip, mac in sorted(self.grants.items())],
                    'firewall': summary['verified']}
