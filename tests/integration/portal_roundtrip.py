"""Real HTTP child, root broker and nftables in an empty mapped network namespace."""
import html
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from thinkisp import config
from thinkisp.core import portal
from thinkisp.portal import daemon


def checked(args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=8).stdout


links = json.loads(checked(['ip', '-j', 'link', 'show']))
mapping = [list(map(int, line.split())) for line in Path('/proc/self/uid_map').read_text().splitlines()]
if (os.geteuid() != 0 or [x['ifname'] for x in links] != ['lo'] or
        not any(start <= 65534 < start + count and count > 1 for start, outer, count in mapping)):
    raise SystemExit('Usa unshare --user --map-auto --map-root-user --setgroups allow --net python3 tests/integration/portal_roundtrip.py')

remote_code = r'''
import http.client,json,subprocess,sys
print('ready',flush=True)
for line in sys.stdin:
    request=json.loads(line)
    try:
        if request['op']=='run':
            p=subprocess.run(request['args'],capture_output=True,text=True,timeout=5)
            result={'code':p.returncode,'output':p.stdout}
        else:
            connection=http.client.HTTPConnection(request['host'],request['port'],timeout=5)
            connection.request(request.get('method','GET'),request.get('path','/'),
                body=request.get('body'),headers=request.get('headers',{}))
            response=connection.getresponse()
            result={'status':response.status,'headers':dict(response.getheaders()),'body':response.read().decode()}
            connection.close()
    except Exception as exc:
        result={'error':str(exc)}
    print(json.dumps(result),flush=True)
'''
remote = subprocess.Popen(['unshare', '--net', sys.executable, '-u', '-c', remote_code],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)


def call(request):
    import select
    remote.stdin.write(json.dumps(request) + '\n')
    remote.stdin.flush()
    if not select.select([remote.stdout], [], [], 8)[0]:
        raise AssertionError('Remote request timed out')
    return json.loads(remote.stdout.readline())


supervisor = None
with tempfile.TemporaryDirectory(prefix='isp-portal-') as temporary:
    try:
        assert remote.stdout.readline().strip() == 'ready'
        checked(['ip', 'link', 'add', 'ap-fixture', 'type', 'veth', 'peer', 'name', 'client-fixture'])
        checked(['ip', 'link', 'set', 'client-fixture', 'netns', str(remote.pid)])
        checked(['ip', 'addr', 'add', '10.32.47.1/24', 'dev', 'ap-fixture'])
        checked(['ip', 'link', 'set', 'ap-fixture', 'up'])
        checked(['ip', 'link', 'set', 'lo', 'up'])
        for args in (['ip','link','set','lo','up'], ['ip','link','set','client-fixture','up'],
                     ['ip','addr','add','10.32.47.2/24','dev','client-fixture'],
                     ['ip','route','add','default','via','10.32.47.1']):
            assert call({'op':'run','args':args})['code'] == 0
        checked(['nft', 'add', 'table', 'inet', 'external_fixture'])
        root = Path(temporary)
        config.STATE_DIR = root
        owner = '11111111-1111-1111-1111-111111111111'
        instance = '22222222-2222-2222-2222-222222222222'
        directory = root / ('portal-' + instance)
        directory.mkdir(mode=0o700)
        filename = directory / 'config.json'
        data = {'owner':owner,'instance':instance,'interface':'ap-fixture','subnet':'10.32.47.0/24',
                'gateway':'10.32.47.1','caller_uid':0,'label':'Una red diferente < & >'}
        filename.write_text(json.dumps(data))
        filename.chmod(0o600)
        active_marker = root / 'active'
        # Only the NetworkManager observation is substituted; all daemon, IPC,
        # privilege separation, HTTP and kernel firewall code is production code.
        def inactive_fixture(selected):
            if active_marker.exists():
                raise daemon.ThinkISPError('El AP sigue activo.')
        daemon.inactive = inactive_fixture
        supervisor = os.fork()
        if supervisor == 0:
            with (root / 'supervisor.log').open('w') as log:
                os.dup2(log.fileno(), 1)
                os.dup2(log.fileno(), 2)
                try:
                    path, parsed = daemon.read_config(str(filename))
                    daemon.serve(path, parsed)
                    os._exit(0)
                except BaseException:
                    traceback.print_exc()
                    log.flush()
                    os._exit(1)
        value = {'schema_version':1,'owner':owner,'owner_name':config.HOTSPOT_CONN,
                 'instance':instance,'directory':str(directory),'phase':'prepared'}
        result = None
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                result = portal._rpc(value,'status')
                portal._healthy(result)
                break
            except (OSError, portal.ThinkISPError):
                time.sleep(0.1)
        assert result and result.get('child_alive'), (root / 'supervisor.log').read_text()
        children = Path(f'/proc/{supervisor}/task/{supervisor}/children').read_text().split()
        assert children, 'No HTTP child'
        status = Path(f'/proc/{children[0]}/status').read_text()
        assert re.search(r'^Uid:\s+65534\s',status,re.M), status
        port = result['port']
        active_marker.touch()
        try:
            portal._rpc(value, 'shutdown')
            raise AssertionError('Guard removed while AP active')
        except portal.ThinkISPError:
            pass
        # An HTTP probe to an arbitrary destination is captured by the real NAT rule.
        redirected = call({'op':'http','host':'203.0.113.9','port':80})
        assert redirected['status']==303, redirected
        assert redirected['headers']['Location']==f'http://10.32.47.1:{port}/'
        page = call({'op':'http','host':'10.32.47.1','port':port})
        assert page['status']==200 and 'Una red diferente &lt; &amp; &gt;' in page['body'], page
        token = html.unescape(re.search(r'name="token" value="([^"]+)"',page['body'])[1])
        accepted = call({'op':'http','host':'10.32.47.1','port':port,'method':'POST','path':'/accept',
                         'body':'token='+token,'headers':{'Content-Type':'application/x-www-form-urlencoded'}})
        assert accepted['status']==303, accepted
        result = portal._rpc(value,'status')
        assert result['clients'][0]['ip']=='10.32.47.2', result
        replay = call({'op':'http','host':'10.32.47.1','port':port,'method':'POST','path':'/accept',
                       'body':'token='+token,'headers':{'Content-Type':'application/x-www-form-urlencoded'}})
        assert replay['status']==403, replay
        portal._rpc(value,'revoke',{'target':'10.32.47.2'})
        assert portal._rpc(value,'status')['clients']==[]
        # A child crash revokes clients but keeps the deny guard and recovery IPC.
        portal._rpc(value,'authorize',{'target':'10.32.47.2'})
        os.kill(int(children[0]),signal.SIGKILL)
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            result=portal._rpc(value,'status')
            if result['phase']=='fault':
                break
            time.sleep(0.1)
        assert result['phase']=='fault' and result['clients']==[],result
        active_marker.unlink()
        assert portal._rpc(value,'shutdown')['stopped'] is True
        os.waitpid(supervisor,0)
        supervisor=None
        checked(['nft','list','table','inet','external_fixture'])
        assert not any(o.get('table',{}).get('name','').startswith('thinkisp_portal_')
                       for o in json.loads(checked(['nft','-j','list','tables']))['nftables'])
        print('PASS: unprivileged HTTP, real redirect/form, single-use nonce, authorize/revoke, child crash fail closed, guarded shutdown and foreign table preservation.')
    finally:
        if supervisor is not None:
            try:
                os.kill(supervisor,signal.SIGTERM)
                os.waitpid(supervisor,0)
            except ProcessLookupError:
                pass
        remote.terminate()
        remote.wait(timeout=5)
