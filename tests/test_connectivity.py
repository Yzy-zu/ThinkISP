"""Transaction tests with independent fake hosts; never operate on host networking."""
import copy
import ipaddress
import json
from contextlib import nullcontext
import unittest
from unittest.mock import patch

from thinkisp.core import connectivity as c
from thinkisp.core.network import ThinkISPError, CommandError
from thinkisp.core.topology import NetworkIntent
from test_topology import inventory, A, B


class Host:
    def __init__(self, same=False, kind='ethernet'):
        self.inventory = inventory(kind=kind, same=same)
        self.inventory['radios']['phy9']['ap_channel_details'] = [{'number':6,'frequency':2437,'band':'bg'}]
        self.inventory['devices'][0]['wireless_mode'] = 'managed' if same else 'AP'
        self.upstream = 'radio-ap' if same else 'entrada'
        self.state = {'network_profiles':{A:{'applied':False}}}
        self.properties = dict(zip(c.PROFILE_FIELDS, ['radio-ap','','0','','auto','yes']))
        self.original = copy.deepcopy(self.properties)
        self.mutations = []
        self.resources = []
        self.links = []
        self.fail = None
        self.active = False
        self.upstream_uuid = B
        self.channel = 6

    def record(self, value):
        self.state['connectivity_runtime'] = copy.deepcopy(value)

    def profile(self, fields, *args, **kwargs):
        role = {'connection.type':'802-11-wireless','802-11-wireless.mode':'ap','ipv4.method':'shared'}
        return {key:(role[key] if key in role else self.properties.get(key,'')) for key in fields}

    def privileged(self, cmd):
        self.mutations.append(cmd)
        if self.fail and self.fail(cmd):
            raise CommandError(cmd, 'fixture rejected')
        if cmd[:2] == ['iw','phy']:
            self.links = [{'ifname':cmd[5],'ifindex':42}]
        elif cmd[:2] == ['iw','dev']:
            self.links = []
        elif 'add' in cmd:
            self.resources.append(cmd)
        elif 'del' in cmd:
            self.resources.remove(['add' if part=='del' else part for part in cmd])
        return ''

    def run(self, cmd, **kwargs):
        if cmd[0] == 'iw':
            return f'type managed\nchannel {self.channel} (2437 MHz)\nwiphy 9'
        if cmd[0] == 'nmcli':
            if '--active' in cmd:
                return A if self.active else ''
            if cmd[1:3] == ['connection','modify']:
                self.mutations.append(cmd)
                values = cmd[6:]
                for key, value in zip(values[::2],values[1::2]):
                    self.properties[key] = '0' if key.endswith('.channel') and not value else value
            return ''
        if 'address' in cmd:
            return json.dumps([{'ifname':self.upstream,'addr_info':[{'family':'inet','local':'192.0.2.2','prefixlen':24}]}])
        if 'link' in cmd:
            return json.dumps(self.links)
        if 'get' in cmd:
            return json.dumps([{'dev':self.upstream}])
        if 'rule' in cmd:
            return json.dumps([{'priority':0,'table':'local'},{'priority':32766,'table':'main'}])
        if 'route' in cmd:
            return json.dumps([{'dst':'default','dev':self.upstream,'gateway':'192.0.2.1','table':'main'}])
        raise AssertionError(cmd)


class TestConnectivityExecution(unittest.TestCase):
    def host(self, same=False, kind='ethernet'):
        host = Host(same, kind)
        patches = {
            'journal':lambda:copy.deepcopy(host.state.get('connectivity_runtime',{})),
            'record':host.record, 'load_state':lambda:host.state,
            'state_lock':lambda:nullcontext(host.state), 'save_state':lambda state:None,
            'collect_inventory':lambda:host.inventory,
            'load_intent':lambda owner:NetworkIntent(ap_interface='radio-ap',upstream_interface=host.upstream),
            'profile_values':host.profile, 'run_cmd':host.run, 'privileged':host.privileged,
            '_current_uuid':lambda iface:host.upstream_uuid if iface==host.upstream else None,
            'reserved_tables':lambda:set(),
            'owned_resource':lambda *args:True,
        }
        for name, value in patches.items():
            p=patch.object(c,name,value);p.start();self.addCleanup(p.stop)
        return host

    def test_wired_usb_and_second_radio_prepare_confirm_cleanup(self):
        for kind in ('ethernet','wifi'):
            with self.subTest(kind=kind):
                host=self.host(kind=kind)
                value=c.prepare(A)
                self.assertEqual(value['ap_interface'],'radio-ap')
                self.assertFalse(value['create_virtual'])
                self.assertEqual(host.properties['ipv6.method'],'disabled')
                c.confirm(A)
                self.assertTrue(host.state['network_profiles'][A]['applied'])
                c.cleanup(A)
                self.assertFalse(host.resources)
                self.assertEqual(host.properties,host.original)
                self.assertEqual(host.state['connectivity_runtime']['phase'],'idle')

    def test_one_radio_preserves_upstream_and_restores_profile(self):
        host=self.host(same=True)
        value=c.prepare(A)
        self.assertNotEqual(value['ap_interface'],host.upstream)
        self.assertEqual(value['updates']['802-11-wireless.channel'],'6')
        self.assertEqual(value['virtual_ifindex'],42)
        c.confirm(A);c.cleanup(A)
        self.assertFalse(host.links)
        self.assertFalse(host.resources)
        self.assertEqual(host.properties,host.original)
        self.assertFalse(any('down' in cmd for cmd in host.mutations))

    def test_fixed_wrong_channel_no_ir_and_unknown_concurrency_reject_before_mutation(self):
        for condition in ('fixed','no_ir','unknown','budget'):
            with self.subTest(condition=condition):
                host=self.host(same=True)
                if condition=='fixed':host.properties['802-11-wireless.channel']='11'
                if condition=='no_ir':host.inventory['radios']['phy9']['ap_channel_details']=[]
                if condition=='unknown':host.inventory['radios']['phy9']['combinations']=[]
                if condition=='budget':
                    host.inventory['devices'].append(dict(host.inventory['devices'][0],name='extra-client'))
                with self.assertRaises(ThinkISPError):c.prepare(A)
                self.assertFalse(host.mutations)
                self.assertNotIn('connectivity_runtime',host.state)

    def test_each_routing_failure_rolls_back_profile_and_virtual(self):
        for failure in ('route','rule'):
            host=self.host(same=True)
            host.fail=lambda cmd:cmd[:4]==['ip','-4',failure,'add']
            # An attempted command may fail before creating the resource.
            with patch.object(c,'owned_resource',side_effect=lambda resource,_:resource['add'] in host.resources):
                with self.assertRaises(ThinkISPError):c.prepare(A)
            self.assertFalse(host.links)
            self.assertFalse(host.resources)
            self.assertEqual(host.properties,host.original)
            self.assertEqual(host.state['connectivity_runtime']['phase'],'idle')

    def test_external_profile_edit_retains_journal(self):
        host=self.host();c.prepare(A)
        host.properties['ipv4.addresses']='198.51.100.1/24'
        with self.assertRaisesRegex(ThinkISPError,'externamente'):c.cleanup(A)
        self.assertEqual(host.state['connectivity_runtime']['phase'],'recovering')
        self.assertTrue(host.resources)

    def test_external_virtual_identity_is_not_deleted(self):
        host=self.host(same=True);c.prepare(A)
        host.links[0]['ifindex']=99
        with self.assertRaisesRegex(ThinkISPError,'identidad'):c.cleanup(A)
        self.assertTrue(host.links)

    def test_active_ap_and_foreign_owner_cannot_be_cleaned(self):
        host=self.host();c.prepare(A)
        with self.assertRaises(ThinkISPError):c.cleanup(B)
        host.active=True
        with self.assertRaises(ThinkISPError):c.cleanup(A)
        self.assertTrue(host.resources)

    def test_changed_input_or_channel_does_not_confirm_success(self):
        for changed in ('uuid','channel'):
            host=self.host(same=True);c.prepare(A)
            if changed=='uuid':host.upstream_uuid=None
            else:host.channel=11
            with self.assertRaises(ThinkISPError):c.confirm(A)
            self.assertFalse(host.state['network_profiles'][A]['applied'])


class TestRoutingAndAllocation(unittest.TestCase):
    def test_routes_precede_vpn_and_fail_closed(self):
        plan=c.route_resources(A,'ap','wan','10.22.0.0/24','192.0.2.1','192.0.2.0/24',
                              [{'priority':0,'table':'local'},{'priority':100,'table':88}],[],[30000])
        self.assertLess(plan['priority'],100)
        additions=[r['add'] for r in plan['resources']]
        self.assertTrue(any('unreachable' in cmd and 'default' in cmd for cmd in additions))
        self.assertTrue(any('unreachable' in cmd and '10.22.0.0/24' in cmd for cmd in additions))
        self.assertEqual(additions[-1][-4:],['iif','ap','lookup',str(plan['table'])])

    def test_no_safe_priority_is_rejected(self):
        with self.assertRaises(ThinkISPError):
            c.route_resources(A,'ap','wan','10.22.0.0/24',None,None,[{'priority':1,'table':88}],[])

    def test_subnet_allocation_avoids_occupied_networks(self):
        chosen=c.select_subnet(A,'',['10.0.0.0/8','172.16.0.0/12'])
        self.assertTrue(ipaddress.ip_interface(chosen).ip in ipaddress.ip_network('192.168.0.0/16'))
        with self.assertRaises(ThinkISPError):c.select_subnet(A,'192.0.2.1/24',['192.0.2.9/32'])
        with self.assertRaises(ThinkISPError):c.select_subnet(A,'',['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'])

    def test_virtual_names_do_not_overwrite_existing_devices(self):
        name=c.virtual_name(A,[])
        self.assertLessEqual(len(name),15)
        self.assertNotEqual(c.virtual_name(A,[name]),name)

    def test_owned_routes_and_rules_missing_changed_and_foreign(self):
        plan=c.route_resources(A,'ap','wan','10.22.0.0/24','192.0.2.1','192.0.2.0/24',[],[])
        for resource in plan['resources']:
            cmd=resource['add']
            if cmd[2]=='rule':
                row={'priority':plan['priority'],'table':plan['table'],'src':'10.22.0.0','srclen':24,'iif':'ap'}
            else:
                unreachable='unreachable' in cmd
                row={'table':plan['table'],'dst':cmd[7 if unreachable else 6],'type':'unreachable' if unreachable else 'unicast'}
                for token,key in (('dev','dev'),('via','gateway'),('metric','metric')):
                    if token in cmd:row[key]=int(cmd[cmd.index(token)+1]) if token=='metric' else cmd[cmd.index(token)+1]
            with patch.object(c,'run_cmd',return_value=json.dumps([row])):
                self.assertTrue(c.owned_resource(resource,plan))
            with patch.object(c,'run_cmd',return_value='[]'):
                self.assertFalse(c.owned_resource(resource,plan))
            if 'dev' in row or 'iif' in row:
                row['dev' if 'dev' in row else 'iif']='foreign'
                with patch.object(c,'run_cmd',return_value=json.dumps([row])):
                    with self.assertRaises(ThinkISPError):c.owned_resource(resource,plan)

    def test_no_arbitrary_privileged_binary_from_journal(self):
        with self.assertRaises(ThinkISPError):c.privileged(['rm','anything'])


class TestActivationLifecycle(unittest.TestCase):
    def test_activation_is_confirmed_before_success_and_failure_is_rolled_back(self):
        from thinkisp.core import wifi
        for failure in (False, True):
            events=[]
            def confirm(owner):
                events.append('confirm')
                if failure:
                    raise ThinkISPError('fixture route mismatch')
            with patch.object(wifi,'profile_uuid',return_value=A), \
                 patch.object(wifi,'load_state',return_value={'network_profiles':{A:{}}}), \
                 patch.object(c,'journal',return_value={}), \
                 patch.object(c,'operation_lock',return_value=nullcontext()), \
                 patch.object(c,'prepare',side_effect=lambda owner:events.append('prepare')), \
                 patch.object(c,'confirm',side_effect=confirm), \
                 patch.object(wifi,'is_hotspot_active',side_effect=[False, True] if failure else [False]), \
                 patch.object(wifi,'_start_hotspot_legacy',side_effect=lambda **kwargs:events.append(('activate',kwargs)) or (True,'ok')), \
                 patch.object(wifi,'notify',side_effect=lambda *args,**kwargs:events.append('notify')), \
                 patch.object(wifi,'run_cmd',side_effect=lambda *args,**kwargs:events.append('down')), \
                 patch.object(wifi,'_cleanup_session',side_effect=lambda owner:events.append('cleanup') or []), \
                 patch.object(wifi,'ap_interface',return_value='fixture-ap'):
                if failure:
                    with self.assertRaises(ThinkISPError):wifi.start_hotspot()
                    self.assertEqual(events[-2:],['down','cleanup'])
                    self.assertNotIn('notify',events)
                else:
                    self.assertEqual(wifi.start_hotspot(),(True,'ok'))
                    self.assertEqual(events,['prepare',('activate',{'announce':False}),'confirm','notify'])

    def test_dynamic_interface_selection_survives_new_process_and_cleanup(self):
        from thinkisp import config
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as directory:
            path=Path(directory)/'state.json'
            with patch.object(config,'STATE_FILE',path):
                self.assertEqual(config.ap_interface('detected-ap'),'detected-ap')
                path.write_text(json.dumps({'connectivity_runtime':{'owner_name':config.HOTSPOT_CONN,'phase':'running',
                                    'ap_interface':'virtual-ap','upstream_interface':'client'}}))
                self.assertEqual(config.ap_interface('detected-ap'),'virtual-ap')
                self.assertEqual(config.upstream_interface('ethernet'),'client')
                path.write_text(json.dumps({'connectivity_runtime':{'owner_name':config.HOTSPOT_CONN,'phase':'idle','ap_interface':'physical-ap'}}))
                self.assertEqual(config.ap_interface('virtual-ap'),'physical-ap')
                self.assertEqual(config.upstream_interface('ethernet'),'ethernet')
                path.write_text('{broken')
                with self.assertRaises(ThinkISPError):config.ap_interface('detected-ap')

    def test_invalid_kernel_json_is_an_operational_error(self):
        for response in ('{broken','{}','[1]'):
            with patch.object(c,'run_cmd',return_value=response):
                with self.assertRaises(ThinkISPError):c.query_json(['ip','-j','link','show'])

    def test_network_plan_cannot_change_during_running_or_pending_session(self):
        from thinkisp.core import deployment
        for phase in ('preparing','prepared','running','recovering'):
            state={'connectivity_runtime':{'phase':phase}}
            with patch.object(deployment,'state_lock',return_value=nullcontext(state)), \
                 patch.object(deployment,'save_state') as save:
                with self.assertRaises(ThinkISPError):deployment.save_intent(A,NetworkIntent())
                save.assert_not_called()

    def test_profile_edits_cannot_persist_runtime_settings(self):
        from thinkisp.core.profile import ensure_profile_editable
        for phase in ('preparing','prepared','running','recovering'):
            with patch('thinkisp.config.runtime_selection',return_value={'phase':phase}):
                with self.assertRaises(ThinkISPError):ensure_profile_editable()


class TestDHCPSubnetValidation(unittest.TestCase):
    def test_custom_range_must_fit_selected_subnet_and_exclude_router(self):
        c.validate_dhcp_range('10.22.0.10,10.22.0.100','10.22.0.1/24')
        for value in ('garbage', '192.168.1.10,192.168.1.100', '10.22.0.1,10.22.0.20',
                      '10.22.0.0,10.22.0.20', '10.22.0.20,10.22.0.255','10.22.0.100,10.22.0.10'):
            with self.assertRaises(ThinkISPError):c.validate_dhcp_range(value,'10.22.0.1/24')
