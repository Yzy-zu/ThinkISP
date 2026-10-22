"""Ejercita el parser nmcli instalado. Nunca contacta ni modifica el daemon."""

import json
import configparser
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from thinkisp.core.network import run_cmd
from thinkisp.core.profile import _offline_template, probe_property, settings_arguments
from thinkisp.ui.setup import BASE_FIELDS


def modify(template, *pairs):
    return run_cmd(['nmcli', '--offline', 'connection', 'modify', *pairs], input_text=template, check=True)


base = _offline_template()
assert 'mode=ap' in base and 'method=shared' in base
secure = modify(base, '802-11-wireless.ssid', 'Prueba: red portátil',
                '802-11-wireless-security.psk', 'OfflineTestPassword',
                '802-11-wireless-security.proto', 'rsn',
                '802-11-wireless.band', 'bg', '802-11-wireless.channel', '6',
                '802-11-wireless.hidden', 'yes', 'ipv4.addresses', '192.0.2.1/24')
assert 'mode=ap' in secure and 'method=shared' in secure and 'hidden=true' in secure
# Exercise the complete form as production serializes it, including defaults.
for band in ('', 'bg', 'a'):
    fields = dict(BASE_FIELDS, **{'802-11-wireless.ssid': 'Prueba automática',
                                '802-11-wireless-security.psk': 'OfflineTestPassword',
                                '802-11-wireless.band': band})
    automatic = modify(base, *settings_arguments(fields))
    parsed = configparser.ConfigParser(interpolation=None)
    parsed.read_string(automatic)
    assert parsed['wifi'].get('channel', '0') == '0'
    assert parsed['wifi'].get('band', '') == band
# Clearing must also remove a pre-existing fixed channel, not preserve it.
reset = modify(secure, *settings_arguments({'802-11-wireless.band':'a', '802-11-wireless.channel':'0'}))
parsed = configparser.ConfigParser(interpolation=None)
parsed.read_string(reset)
assert parsed['wifi']['band'] == 'a' and parsed['wifi'].get('channel', '0') == '0'
upstream = run_cmd(['nmcli', '--offline', 'connection', 'add', 'type', 'ethernet',
                    'con-name', 'thinkISP-offline-upstream', 'ipv4.method', 'auto'], check=True)
modified_dns = modify(upstream, 'ipv4.dns', '192.0.2.53 192.0.2.54', 'ipv4.ignore-auto-dns', 'yes',
                       'ipv6.dns', '', 'ipv6.ignore-auto-dns', 'yes')
assert 'ignore-auto-dns=true' in modified_dns
cake = modify(secure, 'tc.qdiscs', 'root cake')
tbf = modify(secure, 'tc.qdiscs', 'root tbf burst 32768 latency 50 rate 3750000')
assert 'cake' in cake and 'tbf' in tbf
features = {}
for key, value in {
    '802-11-wireless.ap-isolation': '1', '802-11-wireless-security.pmf': '3',
    'ipv4.shared-dhcp-range': '192.0.2.20,192.0.2.100', 'ipv4.shared-dhcp-lease-time': '3600',
    '802-11-wireless.channel-width': '20', '802-11-wireless-security.key-mgmt': 'sae',
    '802-11-wireless.band': '6GHz',
}.items():
    features[key] = probe_property(base if key == '802-11-wireless.band' else secure, key, value)
print(json.dumps({'nmcli': run_cmd(['nmcli', '--version'], check=True), 'offline_profiles': 'PASS',
                  'optional_properties': features, 'complete_automatic_forms': ['auto','bg','a'],
                  'fixed_channel_reset': True, 'network_changes': False}, ensure_ascii=False))
