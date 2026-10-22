"""Control del portal cautivo mediante una tabla nft inet propia."""

import hashlib
import ipaddress
import json
import os
import re
import shutil
import uuid

from thinkisp.core.access import normalize_mac
from thinkisp.core.network import CommandError, ThinkISPError, run_cmd


def portal_table_name(owner: str) -> str:
    """Nombre determinista para la tabla inet del portal según el UUID del propietario."""
    try:
        norm = str(uuid.UUID(str(owner)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ThinkISPError(f"Owner UUID inválido: {owner}") from exc
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
    return f"thinkisp_portal_{digest}"


class PortalFirewall:
    """Gestor del firewall nftables nativo para el portal cautivo builtin."""

    def __init__(self, owner: str, interface: str, subnet: str, gateway: str, port: int, instance: str, *, runner=None):
        try:
            self.owner = str(uuid.UUID(str(owner)))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ThinkISPError(f"Owner UUID inválido: {owner}") from exc

        if (not isinstance(interface, str) or not interface
                or len(interface.encode("utf-8")) > 15
                or re.search(r"[\s/:*?\[\]\\\x00-\x1f\x7f]", interface)):
            raise ThinkISPError(f"El nombre de interfaz no es válido: {interface}")
        self.interface = interface

        if not isinstance(subnet, str) or not isinstance(gateway, str):
            raise ThinkISPError("Subred y puerta de enlace deben ser cadenas de texto.")

        try:
            self.subnet = ipaddress.IPv4Network(subnet, strict=True)
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError, TypeError) as exc:
            raise ThinkISPError(f"Subred IPv4 inválida: {subnet}") from exc

        try:
            self.gateway = ipaddress.IPv4Address(gateway)
        except (ipaddress.AddressValueError, ValueError, TypeError) as exc:
            raise ThinkISPError(f"Puerta de enlace IPv4 inválida: {gateway}") from exc

        if self.gateway not in self.subnet:
            raise ThinkISPError(f"La puerta de enlace {self.gateway} no pertenece a la subred {self.subnet}.")
        if self.gateway == self.subnet.network_address:
            raise ThinkISPError(f"La puerta de enlace no puede ser la dirección de red ({self.gateway}).")
        if self.gateway == self.subnet.broadcast_address:
            raise ThinkISPError(f"La puerta de enlace no puede ser la dirección de broadcast ({self.gateway}).")
        if self.subnet.prefixlen >= 31:
            raise ThinkISPError(f"La subred {self.subnet} no dispone de espacio para clientes y pasarela.")

        if isinstance(port, bool) or not isinstance(port, int) or not (1024 <= port <= 65535):
            raise ThinkISPError(f"Puerto de portal inválido (debe estar en 1024..65535): {port}")
        self.port = port

        try:
            self.instance = str(uuid.UUID(str(instance)))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ThinkISPError(f"Instance UUID inválido: {instance}") from exc

        if runner is None:
            binary = shutil.which("nft")
            if not binary:
                raise ThinkISPError("Instala nftables para gestionar el portal cautivo.")
            self._nft = os.path.abspath(binary)
            self._runner = run_cmd
        else:
            self._nft = shutil.which("nft") or "/usr/sbin/nft"
            self._runner = runner

        self.table = portal_table_name(self.owner)
        self.marker = f"thinkISP portal:{self.owner}:{self.instance}"

    def _run(self, args, input_text=None, check=True, timeout=120):
        cmd = [self._nft, *args]
        try:
            return self._runner(cmd, input_text=input_text, check=check, timeout=timeout)
        except CommandError as exc:
            denied = any(word in exc.detail.lower() for word in ("operation not permitted", "permission denied", "must be root"))
            if denied:
                raise ThinkISPError(f"nftables requiere permisos de administrador (CAP_NET_ADMIN): {exc.detail}") from exc
            raise

    def _run_nft_batch(self, commands):
        payload = json.dumps({"nftables": commands})
        return self._run(["-j", "-f", "-"], input_text=payload)

    def _objects(self, args):
        try:
            data = json.loads(self._run(args))
            objects = data['nftables']
            if not isinstance(objects, list) or any(not isinstance(o, dict) or len(o) != 1 or
                    not isinstance(next(iter(o.values())), dict) for o in objects):
                raise ValueError('objetos nftables inválidos')
            return objects
        except (ValueError, KeyError, TypeError) as exc:
            raise ThinkISPError(f'No se pudo interpretar nftables: {exc}') from exc

    def _list_tables(self):
        objects = self._objects(['-j', 'list', 'tables'])
        if any(set(o) not in ({'table'}, {'metainfo'}) for o in objects):
            raise ThinkISPError('Inventario de tablas nftables inválido.')
        return [o['table'] for o in objects if 'table' in o]

    def _read_table(self):
        if not any(t.get('family') == 'inet' and t.get('name') == self.table for t in self._list_tables()):
            return None
        return {'nftables': self._objects(['-j', 'list', 'table', 'inet', self.table])}

    def _preflight_flowtable_check(self):
        objects = self._objects(['-j', 'list', 'ruleset'])
        if any('flowtable' in o or any('flow' in e or 'flowtable' in e
                for e in o.get('rule', {}).get('expr', [])) for o in objects):
            raise ThinkISPError('Hay flowtable/offload activo; desactívalo antes de usar el portal. No se modificaron sus reglas.')

    def _build_install_batch(self):
        subnet_net = str(self.subnet.network_address)
        subnet_len = self.subnet.prefixlen
        gw_str = str(self.gateway)

        commands = [
            {"create": {"table": {"family": "inet", "name": self.table, "comment": self.marker}}},
            {"add": {"set": {"family": "inet", "table": self.table, "name": "authorized_tuples",
                             "type": ["ether_addr", "ipv4_addr"], "flags": ["timeout"], "timeout": 15}}},
            {"add": {"set": {"family": "inet", "table": self.table, "name": "authorized_ips",
                             "type": "ipv4_addr", "flags": ["timeout"], "timeout": 15}}},
            {"add": {"chain": {"family": "inet", "table": self.table, "name": "prerouting",
                               "type": "nat", "hook": "prerouting", "prio": -101, "policy": "accept"}}},
            {"add": {"chain": {"family": "inet", "table": self.table, "name": "input",
                               "type": "filter", "hook": "input", "prio": -150, "policy": "accept"}}},
            {"add": {"chain": {"family": "inet", "table": self.table, "name": "forward",
                               "type": "filter", "hook": "forward", "prio": -150, "policy": "accept"}}},
            # NAT prerouting: redirect unauthorized HTTP to portal port
            {"add": {"rule": {
                "family": "inet", "table": self.table, "chain": "prerouting",
                "expr": [
                    {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": self.interface}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "ip", "field": "saddr"}},
                               "right": {"prefix": {"addr": subnet_net, "len": subnet_len}}}},
                    {"match": {"op": "!=", "left": {"concat": [
                        {"payload": {"protocol": "ether", "field": "saddr"}},
                        {"payload": {"protocol": "ip", "field": "saddr"}}
                    ]}, "right": "@authorized_tuples"}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}}, "right": 80}},
                    {"redirect": {"port": self.port}}
                ]
            }}},
            # Input: allow DHCP UDP 67
            {"add": {"rule": {
                "family": "inet", "table": self.table, "chain": "input",
                "expr": [
                    {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": self.interface}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "ip", "field": "daddr"}}, "right": {"set": sorted({gw_str, "255.255.255.255", str(self.subnet.broadcast_address)}, key=ipaddress.IPv4Address)}}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "udp", "field": "dport"}}, "right": 67}},
                    {"accept": None}
                ]
            }}},
            # Input: allow DNS UDP 53 to gateway
            {"add": {"rule": {
                "family": "inet", "table": self.table, "chain": "input",
                "expr": [
                    {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": self.interface}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "ip", "field": "saddr"}},
                               "right": {"prefix": {"addr": subnet_net, "len": subnet_len}}}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "ip", "field": "daddr"}}, "right": gw_str}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "udp", "field": "dport"}}, "right": 53}},
                    {"accept": None}
                ]
            }}},
            # Input: allow DNS TCP 53 to gateway
            {"add": {"rule": {
                "family": "inet", "table": self.table, "chain": "input",
                "expr": [
                    {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": self.interface}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "ip", "field": "saddr"}},
                               "right": {"prefix": {"addr": subnet_net, "len": subnet_len}}}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "ip", "field": "daddr"}}, "right": gw_str}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}}, "right": 53}},
                    {"accept": None}
                ]
            }}},
            # Input: allow Portal TCP at portal port to gateway
            {"add": {"rule": {
                "family": "inet", "table": self.table, "chain": "input",
                "expr": [
                    {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": self.interface}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "ip", "field": "saddr"}},
                               "right": {"prefix": {"addr": subnet_net, "len": subnet_len}}}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "ip", "field": "daddr"}}, "right": gw_str}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}}, "right": self.port}},
                    {"accept": None}
                ]
            }}},
            # Input: drop everything else from AP
            {"add": {"rule": {
                "family": "inet", "table": self.table, "chain": "input",
                "expr": [
                    {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": self.interface}},
                    {"drop": None}
                ]
            }}},
            # Forward: AP -> Internet allow only authorized IPv4 tuple in subnet
            {"add": {"rule": {
                "family": "inet", "table": self.table, "chain": "forward",
                "expr": [
                    {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": self.interface}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "ip", "field": "saddr"}},
                               "right": {"prefix": {"addr": subnet_net, "len": subnet_len}}}},
                    {"match": {"op": "==", "left": {"concat": [
                        {"payload": {"protocol": "ether", "field": "saddr"}},
                        {"payload": {"protocol": "ip", "field": "saddr"}}
                    ]}, "right": "@authorized_tuples"}},
                    {"accept": None}
                ]
            }}},
            # Forward: AP -> drop all other traffic
            {"add": {"rule": {
                "family": "inet", "table": self.table, "chain": "forward",
                "expr": [
                    {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": self.interface}},
                    {"drop": None}
                ]
            }}},
            # Forward: Internet -> AP allow only authorized IP with established,related
            {"add": {"rule": {
                "family": "inet", "table": self.table, "chain": "forward",
                "expr": [
                    {"match": {"op": "==", "left": {"meta": {"key": "oifname"}}, "right": self.interface}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": "ip", "field": "daddr"}}, "right": "@authorized_ips"}},
                    {"match": {"op": "in", "left": {"ct": {"key": "state"}}, "right": ["established", "related"]}},
                    {"accept": None}
                ]
            }}},
            # Forward: Internet -> AP drop all other traffic
            {"add": {"rule": {
                "family": "inet", "table": self.table, "chain": "forward",
                "expr": [
                    {"match": {"op": "==", "left": {"meta": {"key": "oifname"}}, "right": self.interface}},
                    {"drop": None}
                ]
            }}},
        ]
        return commands

    def install(self):
        """Crea las tablas y cadenas protegidas atómicamente, rechazando cualquier tabla preexistente."""
        self._preflight_flowtable_check()
        existing = self._list_tables()
        for t in existing:
            if t.get("name") == self.table:
                raise ThinkISPError(
                    f"La tabla {self.table} ya existe en el kernel (familia {t.get('family')}). "
                    "Se rechaza reemplazar o tomar el control de tablas existentes."
                )

        commands = self._build_install_batch()
        self._run_nft_batch(commands)
        self.verify()

    def set_authorized(self, bindings):
        """Actualiza atómicamente los conjuntos autorizados con un lease kernel de 15 segundos."""
        self.verify()
        validated = {}
        mac_to_ip = {}

        for entry in bindings:
            if not isinstance(entry, dict) or "ip" not in entry or "mac" not in entry:
                raise ThinkISPError("Cada enlace de autorización debe ser un diccionario con 'ip' y 'mac'.")
            raw_mac = entry["mac"]
            raw_ip = entry["ip"]
            if not isinstance(raw_mac, str):
                raise ThinkISPError('La dirección MAC debe ser texto.')
            mac = normalize_mac(raw_mac)
            try:
                ip_obj = ipaddress.IPv4Address(str(raw_ip).strip())
            except (ipaddress.AddressValueError, ValueError, TypeError) as exc:
                raise ThinkISPError(f"Dirección IP inválida en autorización: {raw_ip}") from exc

            ip_str = str(ip_obj)
            if ip_obj not in self.subnet:
                raise ThinkISPError(f"La IP {ip_str} está fuera de la subred {self.subnet}.")
            if ip_obj == self.subnet.network_address:
                raise ThinkISPError(f"La IP {ip_str} es la dirección de red de la subred.")
            if ip_obj == self.subnet.broadcast_address:
                raise ThinkISPError(f"La IP {ip_str} es la dirección de broadcast de la subred.")
            if ip_obj == self.gateway:
                raise ThinkISPError(f"La IP {ip_str} coincide con la puerta de enlace.")

            if ip_str in validated and validated[ip_str] != mac:
                raise ThinkISPError(f"Mapeo contradictorio para la IP {ip_str}: {mac} vs {validated[ip_str]}")
            if mac in mac_to_ip and mac_to_ip[mac] != ip_str:
                raise ThinkISPError(f"Mapeo contradictorio para la MAC {mac}: {ip_str} vs {mac_to_ip[mac]}")

            validated[ip_str] = mac
            mac_to_ip[mac] = ip_str

        commands = [
            {"flush": {"set": {"family": "inet", "table": self.table, "name": "authorized_tuples"}}},
            {"flush": {"set": {"family": "inet", "table": self.table, "name": "authorized_ips"}}},
        ]

        if validated:
            elem_tuples = []
            elem_ips = []
            for ip_str, mac in sorted(validated.items()):
                elem_tuples.append({
                    "elem": {
                        "val": {
                            "concat": [mac, ip_str]
                        },
                        "timeout": 15
                    }
                })
                elem_ips.append({
                    "elem": {
                        "val": ip_str,
                        "timeout": 15
                    }
                })
            commands.append({
                "add": {
                    "element": {
                        "family": "inet",
                        "table": self.table,
                        "name": "authorized_tuples",
                        "elem": elem_tuples
                    }
                }
            })
            commands.append({
                "add": {
                    "element": {
                        "family": "inet",
                        "table": self.table,
                        "name": "authorized_ips",
                        "elem": elem_ips
                    }
                }
            })

        self._run_nft_batch(commands)
        observed = self.verify()['bindings']
        if {b['ip']: b['mac'] for b in observed} != validated:
            raise ThinkISPError('nftables no confirmó las autorizaciones solicitadas.')

    def verify(self):
        """Read back the complete owned layout and bounded authorization leases."""
        data = self._read_table()
        if data is None:
            raise ThinkISPError('No se encontró la tabla del portal.')
        expected = {}
        actual = {}
        for command in self._build_install_batch():
            obj = command.get('add', command.get('create'))
            kind, value = next(iter(obj.items()))
            key = (kind, value.get('chain') if kind == 'rule' else value.get('name'))
            expected.setdefault(key, []).append(value)
        bindings, ips = [], []
        for obj in data['nftables']:
            kind, raw = next(iter(obj.items()))
            if kind == 'metainfo':
                continue
            if kind not in ('table', 'chain', 'set', 'rule'):
                raise ThinkISPError('La tabla del portal contiene objetos ajenos.')
            value = {k: v for k, v in raw.items() if k not in ('handle', 'index')}
            if kind == 'set':
                for elem in value.pop('elem', []):
                    item = elem.get('elem', {}) if isinstance(elem, dict) else {}
                    lifetime = item.get('timeout', value.get('timeout'))
                    remaining = item.get('expires', lifetime)
                    if not isinstance(lifetime, (int, float)) or not 0 < lifetime <= 15 or not isinstance(remaining, (int, float)) or not 0 <= remaining <= 15:
                        raise ThinkISPError('Autorización sin vencimiento técnico válido.')
                    val = item.get('val')
                    if value.get('name') == 'authorized_tuples':
                        try:
                            mac, ip = val['concat']
                            bindings.append({'mac': normalize_mac(mac), 'ip': str(ipaddress.IPv4Address(ip))})
                        except (ValueError, KeyError, TypeError, AttributeError) as exc:
                            raise ThinkISPError('Autorización del portal dañada.') from exc
                    else:
                        ips.append(str(val))
            key = (kind, value.get('chain') if kind == 'rule' else value.get('name'))
            actual.setdefault(key, []).append(value)
        if actual != expected:
            raise ThinkISPError('Las reglas del portal cambiaron: no coinciden con la política de esta sesión.')
        if sorted(ips) != sorted(b['ip'] for b in bindings) or len(ips) != len(set(ips)):
            raise ThinkISPError('Los conjuntos de autorización no coinciden.')
        for binding in bindings:
            ip = ipaddress.IPv4Address(binding['ip'])
            if ip not in self.subnet or ip in (self.gateway, self.subnet.network_address, self.subnet.broadcast_address):
                raise ThinkISPError('Una autorización está fuera de la red de clientes.')
        return {'table': self.table, 'owner': self.owner, 'instance': self.instance,
                'interface': self.interface, 'subnet': str(self.subnet), 'gateway': str(self.gateway),
                'port': self.port, 'authorized_count': len(bindings), 'bindings': bindings, 'verified': True}

    def remove(self):
        """Elimina atómicamente la tabla propia si existe y coincide con la instancia; idempotente."""
        data = self._read_table()
        if data is None:
            return
        self.verify()
        commands = [{"delete": {"table": {"family": "inet", "name": self.table}}}]
        self._run_nft_batch(commands)
        actual = self._read_table()
        if actual is not None:
            raise ThinkISPError(f"La tabla {self.table} continúa presente tras el borrado.")
