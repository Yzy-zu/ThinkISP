"""Curses real en un PTY; consultas y mediciones de red usan fixtures."""

import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time

ROOT = Path(__file__).resolve().parents[2]
CHILD = r'''
import curses
import json
import os
import time
from thinkisp.ui import tui
fd = int(os.environ['THINKISP_TEST_EVENTS'])
def report(kind, state, **extra):
    os.write(fd, (json.dumps({'kind': kind, 'modal': state.modal, 'running': state.running, 'page': state.page, **extra}) + '\n').encode())
def poll():
    time.sleep(0.15)
    return {'hotspot_active': True, 'ssid': 'PTY fixture', 'clients': [],
            'current_dns': 'Sistema', 'interface': 'fixture-wifi',
            'instant_down_bps': 1048576, 'instant_up_bps': 131072}
tui.make_poller = lambda state: poll

def measure():
    time.sleep(0.6)
    return {'ping_ms': 8.0, 'down_mbps': 25.0, 'up_mbps': 10.0}
tui.run_speedtest = measure
# Fixtures for every dialog visited here: never invoke real networking tools.
tui.benchmark_dns = lambda: [{'key':'fixture-dns','name':'DNS de prueba','ip':'192.0.2.53','time':8}]
tui.get_blocked_clients = lambda: ['02:00:00:00:00:01']
original_key = tui.handle_keypress
def key(ch, state, width):
    original_key(ch, state, width)
    report('key', state, key=ch)
tui.handle_keypress = key
original_drain = tui.drain_tasks
def drain(state):
    busy = state.busy_label
    original_drain(state)
    if busy and not state.busy_label:
        report('job', state)
tui.drain_tasks = drain
original_header = tui.render_header
last_width = None
def header(win, state, height, width):
    global last_width
    result = original_header(win, state, height, width)
    assert curses.pair_content(4) != curses.pair_content(5), 'Faltan los acentos cian/violeta'
    assert curses.pair_content(18)[1] == 234, 'No se aplicó el fondo oscuro'
    if width != last_width:
        report('resize', state, width=width)
        last_width = width
    return result
tui.render_header = header
def capture(win, state, height, width, suffix=''):
    if os.environ.get('THINKISP_TEST_PREVIEW'):
        from pathlib import Path
        root = Path(os.environ['THINKISP_TEST_PREVIEW'])
        root.mkdir(parents=True, exist_ok=True)
        prefix = (suffix or str(state.page)) + '-' + str(width)
        rows = [win.instr(row,0).decode('utf-8','replace') for row in range(height)]
        (root / (prefix + '.txt')).write_text('\n'.join(rows))
        attributes = [[win.inch(row,col) for col in range(width)] for row in range(height)]
        pairs = {}
        def rgb(number):
            # ncurses may report only the base-eight RGB table for indexed colors.
            if number >= 232:
                return [round((8 + 10 * (number - 232)) * 1000 / 255)] * 3
            if number >= 16:
                index = number - 16
                levels = [0,95,135,175,215,255]
                return [round(levels[value] * 1000 / 255) for value in (index // 36, index // 6 % 6, index % 6)]
            return list(curses.color_content(number))
        for number in range(1,24):
            try:
                fg, bg = curses.pair_content(number)
                pairs[number] = [rgb(fg) if fg >= 0 else [900,900,900],
                                 rgb(bg) if bg >= 0 else [70,70,90]]
            except curses.error:
                pass
        (root / (prefix + '.json')).write_text(json.dumps({'rows':rows,'attrs':attributes,'pairs':pairs,
                                                        'bold':curses.A_BOLD,'reverse':curses.A_REVERSE,'color_mask':curses.A_COLOR}))
original_page = tui.render_page
def page(win, state, height, width):
    result = original_page(win, state, height, width)
    capture(win, state, height, width)
    return result
tui.render_page = page
original_commands = tui.render_commands_modal
def commands(win, state, height, width):
    original_commands(win, state, height, width)
    if state.modal_data.get('query') == 'rendimiento':
        capture(win, state, height, width, 'commands')
tui.render_commands_modal = commands
from thinkisp.ui import setup
from thinkisp.ui.widgets import TerminalUI
from thinkisp.core.profile import Capabilities, Channel
setup.load_configuration = lambda: (
    Capabilities('fixture-wifi','phy7','fixture-driver',[Channel(6,2437,20)]),
    'fixture-uuid',dict(setup.BASE_FIELDS, **{'802-11-wireless.ssid':'Red de prueba'}),None)
from thinkisp.ui import connectivity
from thinkisp.core.topology import NetworkIntent, evaluate_plan
network_inventory = {'devices':[
    {'name':'fixture-wifi','kind':'wifi','managed':True,'phy':'phy7','state':'connected','bus':'other'},
    {'name':'fixture-upstream','kind':'ethernet','managed':True,'phy':None,'state':'connected','bus':'usb'}],
    'radios':{'phy7':{'verified':True,'modes':['managed','AP'],'ap_channels':[6],'combinations':[]}},
    'routes':[{'interface':'fixture-upstream','family':4,'metric':100}],
    'engines':{'none':{'installed':True},'opennds':{'installed':False}},
    'dependencies':{'nft':True},'firewall':{'status':'readable','table_count':0},'errors':[]}
network_intent = NetworkIntent(ap_interface='fixture-wifi')
connectivity.inspect_deployment = lambda: {
    'profile_uuid':'fixture-uuid','profile_name':'fixture','saved_intent':network_intent.to_dict(),
    'inventory':network_inventory,'plan':evaluate_plan(network_inventory,network_intent)}
original_frame = TerminalUI.frame
reported = False
def frame(self, title, *args, **kwargs):
    global reported
    result = original_frame(self, title, *args, **kwargs)
    if title.startswith('Conectividad ·'):
        os.write(fd, (json.dumps({'kind':'connectivity'}) + '\n').encode())
    if title == 'Editar red' and not reported:
        os.write(fd, (json.dumps({'kind':'configuration'}) + '\n').encode())
        reported = True
    return result
TerminalUI.frame = frame
curses.wrapper(tui.run_tui)
'''

master, slave = pty.openpty()
read_fd, write_fd = os.pipe()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 120, 0, 0))
process = subprocess.Popen([sys.executable, '-c', CHILD], cwd=ROOT,
                           stdin=slave, stdout=slave, stderr=slave, pass_fds=(write_fd,),
                           env={**os.environ, 'TERM': 'xterm-256color', 'PYTHONPATH': str(ROOT),
                                'THINKISP_TEST_EVENTS': str(write_fd), 'THINKISP_COLOR': 'always'}, start_new_session=True)
os.close(slave)
os.close(write_fd)
output, pending = bytearray(), bytearray()
events = []


def wait_event(kind, **expected):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        for i, event in enumerate(events):
            if event['kind'] == kind and all(event.get(k) == v for k, v in expected.items()):
                return events.pop(i)
        ready = select.select([master, read_fd], [], [], 0.1)[0]
        for fd in ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b''
            if fd == master:
                output.extend(chunk)
            else:
                pending.extend(chunk)
                while b'\n' in pending:
                    line, _, rest = pending.partition(b'\n')
                    pending[:] = rest
                    events.append(json.loads(line))
    raise AssertionError(f'No llegó {kind} {expected}; eventos: {events}; salida: {bytes(output)[-500:]!r}')


def press(key, modal):
    os.write(master, key)
    event = wait_event('key', key=key[0])
    assert event['modal'] == modal, event
    return event


try:
    wait_event('resize', width=120)
    os.write(master, b'c')
    wait_event('configuration')
    os.write(master, b'\x1b')
    assert wait_event('key', key=ord('c'))['modal'] is None
    os.write(master, b'n')
    wait_event('connectivity')
    os.write(master, b'\x1b')
    assert wait_event('key', key=ord('n'))['modal'] is None
    press(b'?', 'help')
    press(b'\x1b', None)
    press(b'p', 'speedtest')
    press(b'\x1b', None)
    press(b'?', 'help')
    assert wait_event('job')['modal'] == 'help', 'Resultado tardío sustituyó el diálogo'
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
    os.kill(process.pid, signal.SIGWINCH)
    wait_event('resize', width=80)
    press(b'\x1b', None)
    # Native function keys switch all five views at 80x24.
    for sequence, code, page in [(b'\x1bOQ',266,1),(b'\x1bOR',267,2),(b'\x1bOS',268,3),
                                 (b'\x1b[15~',269,4),(b'\x1bOP',265,0)]:
        os.write(master, sequence)
        assert wait_event('key', key=code)['page'] == page
    # Command palette filters while typing and opens a selected view.
    press(b'/', 'commands')
    for letter in b'rendimiento':
        press(bytes([letter]), 'commands')
    assert press(b'\n', None)['page'] == 3
    press(b'\x10', 'commands')
    press(b'q', 'commands')  # q is text inside search, not the global quit shortcut.
    press(b'\x1b', None)
    for shortcut, modal in [(b't','timer'),(b'l','limit'),(b'u','blocked'),(b'd','dns')]:
        press(shortcut, modal)
        press(b'\x1b', None)
        if shortcut in (b'u', b'd'):
            wait_event('job')
    assert not press(b'q', None)['running']
    process.wait(timeout=5)
    assert process.returncode == 0, process.returncode
    assert b'thinkISP' in output and b'Traceback' not in output
    print('PASS: curses real, teclado, editor de conectividad, color, paleta de acciones, cinco vistas, configuración integrada, temporizador, límites, bloqueados, DNS, resultado tardío, resize 80x24 y salida limpia; red sin modificar.')
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
    os.close(master)
    os.close(read_fd)
