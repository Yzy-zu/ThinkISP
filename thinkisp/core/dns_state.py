"""DNS por sesión: cambios temporales, registro previo y restauración por UUID."""

import copy
import re

from thinkisp.config import DNS_PROVIDERS, WLAN_IFACE, detect_eth_iface
from thinkisp.core.network import (
    ThinkISPError, is_hotspot_active, load_state, run_cmd, save_state, split_nmcli, state_lock,
)
from thinkisp.core.profile import profile_uuid, profile_values, require_hotspot_profile


DNS_FIELDS = ('ipv4.dns', 'ipv4.ignore-auto-dns', 'ipv6.dns', 'ipv6.ignore-auto-dns')


def _tokens(value):
    return sorted(filter(None, re.split(r'[,;\s]+', value)))


def _equal(first, second):
    return all((_tokens(first.get(key, '')) == _tokens(second.get(key, '')))
               if key.endswith('.dns') else first.get(key) == second.get(key) for key in DNS_FIELDS)


def active_connections():
    out = run_cmd(['nmcli', '-t', '-f', 'NAME,UUID,DEVICE', 'connection', 'show', '--active'], check=True, strip=False)
    return [parts for line in out.splitlines() for parts in [split_nmcli(line)] if len(parts) == 3]


def upstream_target():
    interface = detect_eth_iface(WLAN_IFACE)
    if not interface or interface == WLAN_IFACE:
        raise ThinkISPError('No se detectó una salida de red distinta del hotspot para configurar DNS.')
    matches = [item for item in active_connections() if item[2] == interface]
    if len(matches) != 1:
        raise ThinkISPError('La salida no tiene un perfil activo inequívoco. Selecciona THINKISP_ETH.')
    return matches[0][1], interface


def _modify(uuid, properties):
    pairs = [part for key in DNS_FIELDS for part in (key, properties[key])]
    # No guardar el override en el perfil persistente del equipo.
    run_cmd(['nmcli', 'connection', 'modify', '--temporary', 'uuid', uuid, *pairs], check=True)


def _reapply(uuid, interface, expected=None):
    live = [item[1] for item in active_connections() if item[2] == interface]
    if live != [uuid]:
        return False
    run_cmd(['nmcli', 'device', 'reapply', interface], check=True)
    if expected is not None:
        actual = run_cmd(['nmcli', '-e', 'no', '-g', 'IP4.DNS,IP6.DNS', 'device', 'show', interface], check=True)
        if _tokens(actual) != _tokens(expected['ipv4.dns'] + ' ' + expected['ipv6.dns']):
            raise ThinkISPError('Los DNS activos del dispositivo no coinciden con los solicitados.')
    return True


def _snapshot(uuid):
    return profile_values(list(DNS_FIELDS), uuid, check=True)


def _restore_owner(state, owner):
    journal = state.setdefault('dns_journal', {}).get(owner, {})
    errors = []
    for uuid, entry in list(journal.items()):
        try:
            current = _snapshot(uuid)
            if not _equal(current, entry['original']):
                if not any(_equal(current, candidate) for candidate in entry.get('managed', [])):
                    raise ThinkISPError('Los DNS cambiaron fuera de thinkISP; no se sobrescribió esa configuración.')
                _modify(uuid, entry['original'])
                if not _equal(_snapshot(uuid), entry['original']):
                    raise ThinkISPError('NetworkManager no confirmó la restauración del perfil DNS.')
            original = entry['original']
            check_live = original if all(original[key] == 'yes' for key in DNS_FIELDS if 'ignore-auto' in key) else None
            _reapply(uuid, entry['interface'], check_live)
            del journal[uuid]
            save_state(state)
        except ThinkISPError as exc:
            errors.append(f'{uuid}: {exc}')
    if not journal:
        state['dns_journal'].pop(owner, None)
    if errors:
        raise ThinkISPError('Restauración DNS pendiente: ' + '; '.join(errors))


def _apply(state, owner, provider):
    uuid, interface = upstream_target()
    # Si cambia la salida, restaurar la anterior antes de tomar otra en control.
    journal = state.setdefault('dns_journal', {}).setdefault(owner, {})
    if any(item != uuid for item in journal):
        _restore_owner(state, owner)
        journal = state['dns_journal'].setdefault(owner, {})
    for other, entries in state['dns_journal'].items():
        if other != owner and uuid in entries:
            raise ThinkISPError('Otro perfil de thinkISP mantiene un cambio DNS en esta salida; restaúralo primero.')
    before = _snapshot(uuid)
    previous_entry = copy.deepcopy(journal.get(uuid))
    if previous_entry and not (_equal(before, previous_entry['original'])
                               or any(_equal(before, candidate) for candidate in previous_entry.get('managed', []))):
        raise ThinkISPError('Los DNS fueron modificados externamente; se conservó el registro para recuperación.')
    desired = {'ipv4.dns': DNS_PROVIDERS[provider]['servers'], 'ipv4.ignore-auto-dns': 'yes',
               'ipv6.dns': '', 'ipv6.ignore-auto-dns': 'yes'}
    journal[uuid] = {'interface': interface, 'original': previous_entry['original'] if previous_entry else before,
                     'managed': [before, desired]}
    # Recuperable incluso si se interrumpe el proceso justo después de modificar NM.
    save_state(state)
    try:
        _modify(uuid, desired)
        if not _equal(_snapshot(uuid), desired):
            raise ThinkISPError('NetworkManager no confirmó los DNS del perfil.')
        if not _reapply(uuid, interface, desired):
            raise ThinkISPError('La conexión de salida cambió durante la aplicación de DNS.')
    except ThinkISPError as exc:
        try:
            current = _snapshot(uuid)
            if not (_equal(current, before) or _equal(current, desired)):
                raise ThinkISPError('La configuración cambió externamente durante la recuperación.')
            _modify(uuid, before)
            if not _equal(_snapshot(uuid), before):
                raise ThinkISPError('No se confirmó el DNS anterior.')
            _reapply(uuid, interface)
            if previous_entry:
                journal[uuid] = previous_entry
            else:
                journal.pop(uuid, None)
            save_state(state)
        except ThinkISPError as recovery:
            raise ThinkISPError(f'{exc}. Recuperación DNS pendiente: {recovery}') from exc
        raise


def configure_dns(provider=None, adblock=None):
    owner = require_hotspot_profile()
    with state_lock() as state:
        preference = copy.deepcopy(state.get('dns_settings', {}).get(owner, {}))
        old_preference = copy.deepcopy(preference)
        if adblock is True:
            if preference.get('provider') != 'adguard':
                preference['before_adblock'] = preference.get('provider')
            provider = 'adguard'
        elif adblock is False:
            provider = preference.pop('before_adblock', None) if preference.get('provider') == 'adguard' else preference.get('provider')
        else:
            preference.pop('before_adblock', None)
        active = is_hotspot_active(check=True)
        if provider:
            if provider not in DNS_PROVIDERS:
                raise ThinkISPError('Proveedor DNS desconocido.')
            if active:
                _apply(state, owner, provider)
        else:
            _restore_owner(state, owner)
        preference['provider'] = provider
        state.setdefault('dns_settings', {})[owner] = preference
        # No reutilizar los indicadores globales de versiones anteriores.
        state.pop('dns', None)
        state.pop('adblock', None)
        try:
            save_state(state)
        except ThinkISPError as exc:
            state['dns_settings'][owner] = old_preference
            try:
                _restore_owner(state, owner)
            except ThinkISPError as recovery:
                raise ThinkISPError(f'{exc}. Restauración pendiente: {recovery}') from exc
            raise
    if not provider:
        return True, 'DNS anterior restaurado; thinkISP dejó de sobrescribir la salida.'
    name = DNS_PROVIDERS[provider]['name']
    return True, f'{name}: ' + ('aplicado y verificado en la salida.' if active else 'guardado para el próximo encendido.')


def apply_saved_dns(owner):
    with state_lock() as state:
        provider = state.get('dns_settings', {}).get(owner, {}).get('provider')
        if provider:
            _apply(state, owner, provider)


def restore_dns(owner=None):
    with state_lock() as state:
        errors = []
        owners = [owner] if owner else list(state.get('dns_journal', {}))
        for current in owners:
            try:
                _restore_owner(state, current)
            except ThinkISPError as exc:
                errors.append(str(exc))
        save_state(state)
        if errors:
            raise ThinkISPError('; '.join(errors))


def dns_status():
    owner = profile_uuid()
    state = load_state()
    provider = state.get('dns_settings', {}).get(owner, {}).get('provider')
    if not provider or provider not in DNS_PROVIDERS:
        return 'Sistema / configuración anterior', False
    name = DNS_PROVIDERS[provider]['name']
    entries = state.get('dns_journal', {}).get(owner, {})
    if len(entries) != 1 or not is_hotspot_active(check=True):
        return name + ' (pendiente)', False
    uuid, entry = next(iter(entries.items()))
    expected = {'ipv4.dns': DNS_PROVIDERS[provider]['servers'], 'ipv4.ignore-auto-dns': 'yes',
                'ipv6.dns': '', 'ipv6.ignore-auto-dns': 'yes'}
    live = [item[1] for item in active_connections() if item[2] == entry['interface']]
    if live != [uuid] or not _equal(_snapshot(uuid), expected):
        return name + ' (sin aplicar)', False
    actual = run_cmd(['nmcli', '-e', 'no', '-g', 'IP4.DNS,IP6.DNS', 'device', 'show', entry['interface']], check=True)
    verified = _tokens(actual) == _tokens(expected['ipv4.dns'])
    return (name if verified else name + ' (sin aplicar)'), verified and provider == 'adguard'
