"""Control de acceso al host/router por MAC mediante una tabla nft propia."""

import hashlib
import ipaddress
import json
import os
import re
import shutil

from thinkisp.config import ap_interface
from thinkisp.config import WLAN_IFACE
from thinkisp.core.network import (
    CommandError, ThinkISPError, is_hotspot_active, load_state, notify,
    run_cmd, save_state, state_lock,
)
from thinkisp.core.profile import require_hotspot_profile


def normalize_mac(value):
    value = value.strip().lower()
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", value):
        raise ThinkISPError("La dirección MAC no es válida.")
    if int(value[:2], 16) & 1 or value == "00:00:00:00:00:00":
        raise ThinkISPError("Selecciona la MAC unicast de un cliente, no una dirección de grupo.")
    return value


def resolve_target(target):
    if re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", target.strip()):
        return normalize_mac(target)
    try:
        address = ipaddress.ip_address(target.strip())
    except ValueError as exc:
        raise ThinkISPError("Indica una dirección IP o MAC válida.") from exc
    if not ap_interface(WLAN_IFACE):
        raise ThinkISPError("No se detectó una interfaz Wi-Fi.")
    output = run_cmd(["ip", "-j", "neigh", "show", "dev", ap_interface(WLAN_IFACE)], check=True)
    try:
        neighbors = json.loads(output)
        matches = {normalize_mac(item['lladdr']) for item in neighbors
                   if item.get('dst') == str(address) and item.get('lladdr')}
    except (ValueError, TypeError, KeyError) as exc:
        raise ThinkISPError("No se pudo interpretar la tabla de vecinos del hotspot.") from exc
    if len(matches) != 1:
        raise ThinkISPError("No hay una MAC inequívoca para esa IP en el hotspot. Usa la MAC del cliente.")
    return matches.pop()


def table_name(profile):
    identity = f"{os.getuid()}:{profile}".encode()
    return "thinkisp_" + hashlib.sha256(identity).hexdigest()[:20]


class Firewall:
    """Usa nft nativo; PolicyKit sólo si faltan permisos y está disponible."""

    def __init__(self, profile, interface=None):
        self.table = table_name(profile)
        self.interface = interface or ap_interface(WLAN_IFACE)
        binary = shutil.which("nft")
        if not binary:
            raise ThinkISPError("Instala nftables para gestionar el acceso de clientes.")
        self.prefix = [binary]

    def run(self, args, payload=None):
        try:
            return run_cmd([*self.prefix, *args], input_text=payload, check=True, timeout=120)
        except CommandError as exc:
            denied = any(word in exc.detail.lower() for word in ("operation not permitted", "permission denied", "must be root"))
            if len(self.prefix) == 1 and denied and os.geteuid() != 0:
                policykit = shutil.which("pkexec")
                if policykit and os.environ.get('THINKISP_NO_AUTH') != '1':
                    self.prefix.insert(0, policykit)
                    return run_cmd([*self.prefix, *args], input_text=payload, check=True, timeout=120)
                raise ThinkISPError("nftables necesita CAP_NET_ADMIN o autorización PolicyKit. Reintenta la recuperación desde una terminal interactiva.") from exc
            raise

    def read(self):
        # No interpretar EPERM, errores de JSON o de transporte como tabla ausente.
        output = self.run(["-j", "list", "tables"])
        try:
            entries = json.loads(output)['nftables']
            exists = any(item.get('table', {}).get('family') == 'netdev'
                         and item.get('table', {}).get('name') == self.table for item in entries)
            if not exists:
                return None
            data = json.loads(self.run(["-j", "list", "table", "netdev", self.table]))
            marker = next(item['table'].get('comment') for item in data['nftables'] if 'table' in item)
            if marker != "thinkISP access control":
                raise ThinkISPError("La tabla de destino no está identificada como propia de thinkISP.")
            return data
        except (ValueError, KeyError, StopIteration, TypeError) as exc:
            raise ThinkISPError("No se pudo verificar la respuesta JSON de nftables.") from exc

    def apply(self, macs):
        macs = sorted({normalize_mac(mac) for mac in macs})
        old = self.read()
        table = {'family': 'netdev', 'name': self.table}
        commands = [{'delete': {'table': table}}] if old is not None else []
        if macs:
            if not self.interface:
                raise ThinkISPError("No se detectó una interfaz Wi-Fi para aplicar los bloqueos.")
            commands += [
                {'add': {'table': {**table, 'comment': 'thinkISP access control'}}},
                {'add': {'set': {'family': 'netdev', 'table': self.table, 'name': 'blocked',
                                 'type': 'ether_addr', 'elem': macs}}},
                {'add': {'chain': {'family': 'netdev', 'table': self.table, 'name': 'ingress',
                                   'type': 'filter', 'hook': 'ingress', 'dev': self.interface,
                                   'prio': -500, 'policy': 'accept'}}},
                {'add': {'rule': {'family': 'netdev', 'table': self.table, 'chain': 'ingress',
                                  'expr': [
                                      {'match': {'op': '==', 'left': {'payload': {'protocol': 'ether', 'field': 'saddr'}}, 'right': '@blocked'}},
                                      {'drop': None}]}}},
            ]
        if commands:
            self.run(['-j', '-f', '-'], json.dumps({'nftables': commands}))
        actual = self.read()
        if not macs:
            if actual is not None:
                raise ThinkISPError("La tabla de bloqueo continúa aplicada.")
            return
        self.verify(actual, macs)

    def verify(self, data, macs):
        if data is None:
            raise ThinkISPError("nftables no confirmó la tabla de bloqueo.")
        objects = data.get('nftables', [])
        chain = next((item['chain'] for item in objects if item.get('chain', {}).get('name') == 'ingress'), {})
        blocked = next((item['set'] for item in objects if item.get('set', {}).get('name') == 'blocked'), {})
        observed = set()
        for item in blocked.get('elem', []):
            value = item if isinstance(item, str) else item.get('elem', {}).get('val')
            if value:
                observed.add(normalize_mac(value))
        expected_match = {'match': {'op': '==', 'left': {'payload': {'protocol': 'ether', 'field': 'saddr'}}, 'right': '@blocked'}}
        rules = [item['rule'].get('expr', []) for item in objects if item.get('rule', {}).get('chain') == 'ingress']
        if (chain.get('dev') != self.interface or chain.get('hook') != 'ingress'
                or chain.get('policy') != 'accept' or chain.get('prio') != -500
                or blocked.get('type') != 'ether_addr' or observed != set(macs)
                or not any(expected_match in rule and {'drop': None} in rule for rule in rules)):
            raise ThinkISPError("Las reglas leídas del kernel no coinciden con el bloqueo solicitado.")


def blocked_clients():
    profile = require_hotspot_profile()
    records = load_state().get('access_control', {}).get(profile, {})
    return sorted({normalize_mac(mac) for mac in records.get('macs', [])})


def set_client_block(target, blocked=True):
    mac = resolve_target(target) if blocked else normalize_mac(target)
    profile = require_hotspot_profile()
    with state_lock() as state:
        records = state.setdefault('access_control', {})
        previous = records.get(profile, {}).get('macs', [])
        desired = set(previous)
        desired.add(mac) if blocked else desired.discard(mac)
        active = is_hotspot_active(check=True)
        pending = state.get('pending_start', {})
        starting = pending.get('profile_uuid') == profile and not pending.get('closing')
        recovering = profile in state.get('access_journal', {})
        firewall = Firewall(profile) if active or starting or recovering else None
        if firewall:
            state.setdefault('access_journal', {})[profile] = {
                'previous': list(previous), 'interface': ap_interface(WLAN_IFACE),
            }
            save_state(state)  # Recuperación de un cambio interrumpido, no éxito.
            try:
                firewall.apply(desired if active or starting else [])
            except ThinkISPError as exc:
                try:
                    firewall.apply(previous if active or starting else [])
                    state['access_journal'].pop(profile, None)
                    save_state(state)
                except ThinkISPError as recovery:
                    raise ThinkISPError(f"{exc}. No se pudo recuperar la política anterior: {recovery}") from exc
                raise
        records[profile] = {'macs': sorted(desired), 'interface': ap_interface(WLAN_IFACE)}
        active_rules = state.setdefault('access_active', {})
        previous_active = active_rules.get(profile)
        if firewall and desired and (active or starting):
            active_rules[profile] = {'interface': ap_interface(WLAN_IFACE)}
        elif firewall:
            active_rules.pop(profile, None)
        state.get('access_journal', {}).pop(profile, None)
        try:
            save_state(state)
        except ThinkISPError as exc:
            if firewall:
                try:
                    firewall.apply(previous if active or starting else [])
                    records[profile] = {'macs': list(previous), 'interface': ap_interface(WLAN_IFACE)}
                    if previous_active:
                        active_rules[profile] = previous_active
                    else:
                        active_rules.pop(profile, None)
                    save_state(state)
                except ThinkISPError as recovery:
                    raise ThinkISPError(f"{exc}. La política anterior no pudo recuperarse: {recovery}") from exc
            raise
    action = "bloqueado" if blocked else "desbloqueado"
    scope = "acceso al equipo y a Internet" if active else "guardado para el próximo encendido"
    message = f"{mac}: {action} ({scope})."
    notify("thinkISP · Control de acceso", message)
    return True, message


def apply_saved_blocks(profile):
    with state_lock() as state:
        records = state.get('access_control', {}).get(profile, {})
        recovering = profile in state.get('access_journal', {})
        if records.get('macs') or recovering or profile in state.get('access_active', {}):
            state.setdefault('access_active', {})[profile] = {'interface': ap_interface(WLAN_IFACE)}
            save_state(state)
            Firewall(profile).apply(records.get('macs', []))
            if not records.get('macs'):
                state['access_active'].pop(profile, None)
            if recovering:
                state['access_journal'].pop(profile, None)
            save_state(state)


def clear_applied_blocks(profile):
    with state_lock() as state:
        records = state.get('access_control', {}).get(profile, {})
        pending = state.get('access_journal', {}).get(profile)
        if profile in state.get('access_active', {}) or pending:
            Firewall(profile, records.get('interface') or (pending or {}).get('interface')).apply([])
            state.get('access_active', {}).pop(profile, None)
            if pending:
                state['access_journal'].pop(profile, None)
            save_state(state)
