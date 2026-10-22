"""Identity-bound grants, fault behavior and real bounded local framing."""
import copy
import ipaddress
import socket
import unittest
from unittest.mock import patch

from thinkisp.errors import ThinkISPError
from thinkisp.portal.broker import PortalBroker, recv_json, send_json

IP='10.117.83.20'
MAC='02:11:22:33:44:55'


class Firewall:
    subnet=ipaddress.ip_network('10.117.83.0/24')
    gateway=ipaddress.ip_address('10.117.83.1')
    interface='ap-other'
    def __init__(self):
        self.entries=[]
        self.fail=False
    def set_authorized(self, bindings):
        if self.fail:raise ThinkISPError('fixture nft failure')
        self.entries=copy.deepcopy(bindings)
    def verify(self):return {'verified':True}


class TestBroker(unittest.TestCase):
    def setUp(self):
        self.fw=Firewall()
        self.neighbors={IP:MAC}
        self.now=0
        self.b=PortalBroker(self.fw,lambda:dict(self.neighbors),lambda:self.now)

    def token(self):
        return self.b.child_call('challenge',{'ip':IP})['token']

    def accept(self,token,ip=IP):
        return self.b.child_call('accept',{'ip':ip,'token':token})

    def test_nonce_single_use_expiry_and_new_instance(self):
        token=self.token()
        self.assertTrue(self.accept(token)['authorized'])
        with self.assertRaises(ThinkISPError):self.accept(token)
        self.assertEqual(self.fw.entries,[{'ip':IP,'mac':MAC}])
        token=self.token()
        self.now=121
        with self.assertRaises(ThinkISPError):self.accept(token)
        other=PortalBroker(Firewall(),lambda:self.neighbors)
        with self.assertRaises(ThinkISPError):other.child_call('accept',{'ip':IP,'token':self.token()})

    def test_nonce_cannot_be_transferred_by_ip_or_mac(self):
        for change in ('ip','mac'):
            token=self.token()
            self.neighbors[IP]=MAC
            if change=='ip':
                self.neighbors['10.117.83.21']=MAC
                ip='10.117.83.21'
            else:
                self.neighbors[IP]='02:11:22:33:44:66';ip=IP
            with self.assertRaises(ThinkISPError):self.accept(token,ip)
            self.assertEqual(self.fw.entries,[])

    def test_get_status_never_authorizes_and_proxy_fields_rejected(self):
        self.assertFalse(self.b.child_call('client_status',{'ip':IP})['authorized'])
        self.token()
        self.assertEqual(self.fw.entries,[])
        for op,payload in [('authorize',{'ip':IP}),('accept',{'ip':IP,'token':'x','mac':MAC}),
                           ('client_status',{'ip':IP,'forwarded_for':IP})]:
            with self.assertRaises(ThinkISPError):self.b.child_call(op,payload)

    def test_revoke_disconnected_client_and_never_restore_grant(self):
        self.b.authorize(IP)
        self.neighbors.clear()
        self.b.revoke(MAC)
        self.assertEqual(self.fw.entries,[])
        self.neighbors[IP]=MAC
        self.b.refresh()
        self.assertEqual(self.fw.entries,[])

    def test_neighbor_drift_or_disappearance_revokes(self):
        for replacement in (None,'02:11:22:33:44:66'):
            self.neighbors[IP]=MAC
            self.b.authorize(IP)
            self.neighbors.clear()
            if replacement:self.neighbors[IP]=replacement
            self.b.refresh()
            self.assertEqual(self.fw.entries,[])

    def test_ambiguous_mac_and_outside_subnet_refused(self):
        self.neighbors['10.117.83.21']=MAC
        with self.assertRaises(ThinkISPError):self.b.authorize(MAC)
        for ip in ('203.0.113.1','10.117.83.1','10.117.83.0','10.117.83.255','::1'):
            with self.assertRaises(ThinkISPError):self.b.authorize(ip)

    def test_transaction_failure_is_fault_never_success(self):
        token=self.token()
        self.fw.fail=True
        with self.assertRaises(ThinkISPError):self.accept(token)
        self.assertTrue(self.b.fault)
        self.assertEqual(self.b.grants,{})
        with self.assertRaises(ThinkISPError):self.b.authorize(IP)

    def test_child_loss_revokes_and_keeps_guard(self):
        self.b.authorize(IP)
        self.b.fail('HTTP child exited')
        self.assertEqual(self.fw.entries,[])
        self.assertEqual(self.b.status()['phase'],'fault')

    def test_neighbor_read_error_does_not_refresh_old_grants(self):
        self.b.authorize(IP)
        self.b.neighbors=lambda:(_ for _ in ()).throw(ThinkISPError('fixture ip denied'))
        with self.assertRaises(ThinkISPError):self.b.refresh()
        self.assertEqual(self.fw.entries,[])

    def test_real_neighbor_parser_filters_outside_and_rejects_ambiguity(self):
        from thinkisp.portal import broker
        self.b.neighbors=self.b._neighbors
        with patch.object(broker,'run_cmd',return_value='[{"dst":"203.0.113.1","lladdr":"02:11:22:33:44:55","state":["REACHABLE"]}]'):
            with self.assertRaises(ThinkISPError):self.b.authorize(MAC)
        with patch.object(broker,'run_cmd',side_effect=ThinkISPError('permission denied')):
            with self.assertRaises(ThinkISPError):self.b.child_call('challenge',{'ip':IP})


class TestLocalFraming(unittest.TestCase):
    def pair(self):
        a,b=socket.socketpair()
        a.settimeout(1);b.settimeout(1)
        self.addCleanup(a.close);self.addCleanup(b.close)
        return a,b

    def test_real_json_roundtrip_and_sequential_frames(self):
        a,b=self.pair()
        for message in ({'op':'status'}, {'value':'Sí, otra PC'}):
            send_json(a,message)
            self.assertEqual(recv_json(b),message)

    def test_invalid_shapes_eof_and_oversized_frames_fail(self):
        for value in (b'[]\n',b'null\n',b'\xff\n',b'x'*65536+b'\n'):
            a,b=self.pair()
            a.sendall(value)
            with self.assertRaises(ThinkISPError):recv_json(b)
        a,b=self.pair();a.close()
        with self.assertRaises(ThinkISPError):recv_json(b)
