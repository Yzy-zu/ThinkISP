import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from thinkisp.core.access import Firewall


def checked(args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout

# Rechazar la ejecución en la red del equipo: se exige un user namespace
# mapeado y una red vacía con sólo loopback, antes de crear ninguna interfaz.
import os
mapping = Path('/proc/self/uid_map').read_text().split()
interfaces = json.loads(checked(['ip', '-j', 'link', 'show']))
if (os.geteuid() != 0 or len(mapping) != 3 or mapping[0] != '0' or mapping[2] != '1'
        or [item['ifname'] for item in interfaces] != ['lo']):
    raise SystemExit('Usa: unshare --user --map-root-user --net python3 tests/integration/nft_roundtrip.py')

client_source = r'''
import json, subprocess, sys
print('ready', flush=True)
for line in sys.stdin:
    args = json.loads(line)
    result = subprocess.run(args, capture_output=True, text=True)
    print(json.dumps({'code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}), flush=True)
'''
client = subprocess.Popen(['unshare', '--net', sys.executable, '-u', '-c', client_source], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def remote(args):
    client.stdin.write(json.dumps(args) + '\n')
    client.stdin.flush()
    result = json.loads(client.stdout.readline())
    return result

try:
    assert client.stdout.readline().strip() == 'ready', 'Could not create client namespace'
    checked(['ip', 'link', 'add', 'isp-test', 'type', 'veth', 'peer', 'name', 'client-test'])
    checked(['ip', 'link', 'set', 'client-test', 'netns', str(client.pid)])
    checked(['ip', 'link', 'set', 'isp-test', 'up'])
    checked(['ip', 'addr', 'add', '192.0.2.1/24', 'dev', 'isp-test'])
    checked(['ip', '-6', 'addr', 'add', '2001:db8::1/64', 'dev', 'isp-test', 'nodad'])
    for args in [ ['ip', 'link', 'set', 'lo', 'up'], ['ip', 'link', 'set', 'client-test', 'up'],
                  ['ip', 'addr', 'add', '192.0.2.2/24', 'dev', 'client-test'],
                  ['ip', '-6', 'addr', 'add', '2001:db8::2/64', 'dev', 'client-test', 'nodad'] ]:
        result = remote(args)
        assert result['code'] == 0, result
    result = remote(['ip', '-j', 'link', 'show', 'client-test'])
    mac = json.loads(result['stdout'])[0]['address']
    checked(['nft', 'add', 'table', 'inet', 'external_fixture'])
    firewall = Firewall('integration-profile', 'isp-test')
    def ping(address):
        return remote(['ping', '-n', '-c', '1', '-W', '1', address])['code']
    assert ping('192.0.2.1') == 0, 'baseline IPv4 failed'
    assert ping('2001:db8::1') == 0, 'baseline IPv6 failed'
    firewall.apply([mac])
    assert ping('192.0.2.1') != 0, 'IPv4 was not blocked'
    assert ping('2001:db8::1') != 0, 'IPv6 was not blocked'
    firewall.apply([])
    assert ping('192.0.2.1') == 0, 'IPv4 did not recover'
    assert ping('2001:db8::1') == 0, 'IPv6 did not recover'
    checked(['nft', 'list', 'table', 'inet', 'external_fixture'])
    print('PASS: native nftables JSON, IPv4/IPv6 blocked and recovered, external table preserved.')
finally:
    client.terminate()
    client.wait(timeout=5)
