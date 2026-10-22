"""Portal lifecycle transactions on independent fake hosts; no daemon or network writes."""
import copy
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from thinkisp import config
from thinkisp.core import portal as p, wifi
from thinkisp.core.topology import NetworkIntent
from thinkisp.errors import ThinkISPError

OWNER='11111111-1111-1111-1111-111111111111'
OTHER='22222222-2222-2222-2222-222222222222'


class TestPortalRuntime(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory(prefix='isp-unit-')
        self.addCleanup(directory.cleanup)
        self.directory=Path(directory.name)
        self.state={'network_profiles':{OWNER:{'schema_version':1,'intent':NetworkIntent().to_dict(),'applied':False}}}
        self.patches=[
            patch.object(config,'STATE_DIR',self.directory),
            patch.object(p,'load_state',side_effect=lambda:copy.deepcopy(self.state)),
            patch.object(p,'state_lock',side_effect=lambda:nullcontext(self.state)),
            patch.object(p,'save_state'),
            patch.object(p,'run_cmd',return_value=''),
            patch.object(p,'profile_values',side_effect=lambda fields,*args,**kwargs:
                {f:{'connection.type':'802-11-wireless','802-11-wireless.mode':'ap','802-11-wireless.ssid':'Different host','connection.autoconnect':'no'}.get(f,'') for f in fields}),
            patch.object(p,'load_intent',return_value=NetworkIntent(portal_engine='builtin')),
            patch.object(p.os,'geteuid',return_value=0),
            patch.object(p.shutil,'which',side_effect=lambda name:'/usr/bin/'+name),
        ]
        for item in self.patches:
            item.start();self.addCleanup(item.stop)
        self.process=Mock()
        self.process.poll.return_value=None
        self.launch=patch.object(p.subprocess,'Popen',return_value=self.process)
        self.launcher=self.launch.start();self.addCleanup(self.launch.stop)

    def enable(self):
        p.configure(OWNER,True)

    def ready(self, value, op, payload=None):
        if op=='shutdown':
            return {'stopped':True,'instance':value['instance']}
        return {'owner':OWNER,'instance':value['instance'],'phase':'ready','child_alive':True,
                'firewall':True,'port':49181,'clients':[]}

    def prepare(self):
        self.enable()
        with patch.object(p,'_rpc',side_effect=self.ready):
            return p.prepare(OWNER,{'owner':OWNER,'phase':'prepared','ap_interface':'ap-another',
                            'updates':{'ipv4.addresses':'10.117.83.1/24'}})

    def test_legacy_profiles_remain_disabled_without_mutations(self):
        self.assertFalse(p.settings(OWNER)['enabled'])
        p.preflight(OWNER)
        self.assertIsNone(p.prepare(OWNER,None))
        self.launcher.assert_not_called()
        self.assertNotIn('portal_runtime',self.state)

    def test_enable_selects_native_engine_without_network_changes(self):
        self.enable()
        self.assertTrue(p.settings(OWNER)['enabled'])
        self.assertEqual(self.state['network_profiles'][OWNER]['intent']['portal_engine'],'builtin')
        self.launcher.assert_not_called()
        p.configure(OWNER,False)
        self.assertFalse(p.settings(OWNER)['enabled'])

    def test_missing_plan_active_ap_and_pending_connectivity_block_configuration(self):
        self.state['network_profiles']={}
        with self.assertRaises(ThinkISPError):self.enable()
        self.state['network_profiles']={OWNER:{'schema_version':1,'intent':NetworkIntent().to_dict()}}
        with patch.object(p,'run_cmd',return_value=OWNER),self.assertRaises(ThinkISPError):
            self.enable()
        self.state['connectivity_runtime']={'phase':'recovering'}
        with self.assertRaises(ThinkISPError):self.enable()
        self.assertNotIn('portal_profiles',self.state)

    def test_autoconnect_cannot_bypass_supervised_start(self):
        self.enable()
        with patch.object(p, 'profile_values', return_value={'connection.autoconnect':'yes'}):
            with self.assertRaisesRegex(ThinkISPError, 'autoconnect=no'):
                p.preflight(OWNER)
        self.launcher.assert_not_called()

    def test_corrupt_settings_never_silently_disable(self):
        for value in ([],None,{'schema_version':1,'enabled':'false'},{'schema_version':2,'enabled':False}):
            self.state['portal_profiles']={OWNER:value}
            with self.subTest(value=value),self.assertRaises(ThinkISPError):p.settings(OWNER)

    def test_prepared_start_confirm_and_cleanup(self):
        value=self.prepare()
        self.assertEqual(value['phase'],'prepared')
        path=Path(value['directory'])
        data=json.loads((path/'config.json').read_text())
        self.assertEqual(data['gateway'],'10.117.83.1')
        self.assertEqual(data['interface'],'ap-another')
        self.assertNotIn('password',data)
        self.assertEqual((path/'config.json').stat().st_mode & 0o777,0o600)
        with patch.object(p,'_rpc',side_effect=self.ready):
            p.confirm(OWNER)
            self.assertEqual(p.status(OWNER)['url'],'http://10.117.83.1:49181/')
            p.cleanup(OWNER)
        self.assertFalse(path.exists())
        self.assertEqual(p.journal()['phase'],'idle')

    def test_launcher_failure_preserves_then_recovers_journal(self):
        self.enable()
        self.launcher.side_effect=OSError('fixture denied')
        with self.assertRaises(ThinkISPError):
            p.prepare(OWNER,{'owner':OWNER,'phase':'prepared','ap_interface':'ap-other',
                            'updates':{'ipv4.addresses':'10.117.83.1/24'}})
        value=p.journal()
        self.assertEqual(value['phase'],'preparing')
        self.assertTrue(Path(value['directory']).exists())
        with patch.object(p,'_rpc',side_effect=ConnectionRefusedError()):
            p.cleanup(OWNER)
        self.assertEqual(p.journal()['phase'],'idle')

    def test_refuse_foreign_active_or_modified_recovery_identity(self):
        value=self.prepare()
        for owner in (OTHER,):
            with self.assertRaises(ThinkISPError):p.cleanup(owner)
        with patch.object(p,'run_cmd',return_value=OWNER),patch.object(p,'_rpc') as rpc:
            with self.assertRaises(ThinkISPError):p.cleanup(OWNER)
            rpc.assert_not_called()
        value['directory']='/tmp/not-our-portal'
        self.state['portal_runtime']=value
        with self.assertRaises(ThinkISPError):p.cleanup(OWNER)

    def test_status_reports_fault_and_failed_shutdown_preserves_evidence(self):
        value=self.prepare()
        with patch.object(p,'_rpc',side_effect=ThinkISPError('foreign rules')):
            self.assertEqual(p.status(OWNER)['phase'],'recovery_pending')
            with self.assertRaises(ThinkISPError):p.health_check(OWNER)
            with self.assertRaises(ThinkISPError):p.cleanup(OWNER)
        self.assertTrue(Path(value['directory']).exists())
        self.assertEqual(p.journal()['phase'],'recovering')

    def test_unsafe_engine_and_missing_tools_do_not_launch(self):
        self.enable()
        with patch.object(p,'load_intent',return_value=NetworkIntent(portal_engine='opennds')):
            with self.assertRaises(ThinkISPError):p.preflight(OWNER)
        with patch.object(p.shutil,'which',return_value=None):
            with self.assertRaises(ThinkISPError):p.preflight(OWNER)
        self.launcher.assert_not_called()


class TestPortalActivationOrder(unittest.TestCase):
    def test_guard_precedes_ap_and_success_follows_both_confirmations(self):
        from thinkisp.core import connectivity as c
        for fail in (False, True):
            events=[]
            def confirm(owner):
                events.append('portal-confirm')
                if fail:raise ThinkISPError('fixture HTTP child lost')
            with patch.object(wifi,'profile_uuid',return_value=OWNER), \
                 patch.object(wifi,'load_state',return_value={'network_profiles':{OWNER:{}}}), \
                 patch.object(c,'journal',return_value={}), \
                 patch.object(c,'operation_lock',return_value=nullcontext()), \
                 patch.object(c,'prepare',side_effect=lambda owner:events.append('connectivity') or {}), \
                 patch.object(c,'confirm',side_effect=lambda owner:events.append('routes-confirm')), \
                 patch.object(p,'preflight'),patch.object(p,'settings',return_value={'enabled':True}), \
                 patch.object(p,'prepare',side_effect=lambda *args:events.append('guard')), \
                 patch.object(p,'confirm',side_effect=confirm), \
                 patch.object(wifi,'is_hotspot_active',side_effect=[False,True] if fail else [False]), \
                 patch.object(wifi,'_start_hotspot_legacy',side_effect=lambda **kw:events.append('AP') or (True,'ok')), \
                 patch.object(wifi,'notify',side_effect=lambda *args,**kw:events.append('success')), \
                 patch.object(wifi,'run_cmd',side_effect=lambda *args,**kw:events.append('AP-down')), \
                 patch.object(wifi,'_cleanup_session',side_effect=lambda owner:events.append('cleanup') or []):
                if fail:
                    with self.assertRaises(ThinkISPError):wifi.start_hotspot()
                    self.assertEqual(events[-2:],['AP-down','cleanup'])
                    self.assertNotIn('success',events)
                else:
                    wifi.start_hotspot()
                    self.assertEqual(events,['connectivity','guard','AP','routes-confirm','portal-confirm','success'])
