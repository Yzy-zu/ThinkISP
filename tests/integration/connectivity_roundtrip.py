"""Exercise policy routing against Linux in an empty user/network namespace only."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from thinkisp.core.connectivity import route_resources, owned_resource


def run(args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


mapping = Path('/proc/self/uid_map').read_text().split()
if (os.geteuid() != 0 or len(mapping) != 3 or mapping[0] != '0' or mapping[2] != '1'
        or [item['ifname'] for item in json.loads(run(['ip','-j','link','show']).stdout)] != ['lo']):
    raise SystemExit('Usa unshare --user --map-root-user --net python3 tests/integration/connectivity_roundtrip.py')

for iface, address in [('ap-fixture','10.31.0.1/24'),('wan-fixture','192.0.2.2/24'),('other-wan','198.51.100.2/24')]:
    run(['ip','link','add',iface,'type','dummy'])
    run(['ip','link','set',iface,'up'])
    run(['ip','addr','add',address,'dev',iface])
run(['ip','route','add','default','via','198.51.100.1','dev','other-wan'])
plan=route_resources('namespace-test','ap-fixture','wan-fixture','10.31.0.0/24','192.0.2.1','192.0.2.0/24',
                     json.loads(run(['ip','-j','-4','rule','show']).stdout),
                     json.loads(run(['ip','-j','-4','route','show','table','all']).stdout))
for resource in plan['resources']:
    run(resource['add'])
    try:
        assert owned_resource(resource,plan), resource
    except Exception:
        print(run(['ip','-j','-4','rule','show']).stdout)
        raise
lookup=['ip','-j','-4','route','get','203.0.113.1','from','10.31.0.2','iif','ap-fixture']
assert json.loads(run(lookup).stdout)[0]['dev']=='wan-fixture', 'wrong upstream selected'
run(['ip','link','del','wan-fixture'])
assert run(lookup,check=False).returncode != 0, 'fell through to another WAN after upstream loss'
for resource in reversed(plan['resources']):
    if owned_resource(resource,plan):
        run(resource['delete'])
assert json.loads(run(['ip','-j','route','show','default']).stdout)[0]['dev']=='other-wan'
print('PASS: selected upstream, fail closed on removal, owned cleanup, other WAN preserved.')
