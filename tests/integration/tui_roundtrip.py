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
    os.write(fd, (json.dumps({'kind': kind, 'modal': state.modal, 'running': state.running, **extra}) + '\n').encode())
def poll():
    time.sleep(0.15)
    return {'hotspot_active': True, 'ssid': 'PTY fixture', 'clients': [],
            'current_dns': 'Sistema', 'interface': 'fixture-wifi'}
tui.make_poller = lambda state: poll

def measure():
    time.sleep(0.6)
    return {'ping_ms': 8.0, 'down_mbps': 25.0, 'up_mbps': 10.0}
tui.run_speedtest = measure
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
    if width != last_width:
        report('resize', state, width=width)
        last_width = width
    return result
tui.render_header = header
curses.wrapper(tui.run_tui)
'''

master, slave = pty.openpty()
read_fd, write_fd = os.pipe()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 120, 0, 0))
process = subprocess.Popen([sys.executable, '-c', CHILD], cwd=ROOT,
                           stdin=slave, stdout=slave, stderr=slave, pass_fds=(write_fd,),
                           env={**os.environ, 'TERM': 'xterm-256color', 'PYTHONPATH': str(ROOT),
                                'THINKISP_TEST_EVENTS': str(write_fd)}, start_new_session=True)
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
    assert not press(b'q', None)['running']
    process.wait(timeout=5)
    assert process.returncode == 0, process.returncode
    assert b'thinkISP' in output and b'Traceback' not in output
    print('PASS: curses real, teclado, diálogos, resultado tardío, resize 80x24 y salida limpia; red sin modificar.')
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
    os.close(master)
    os.close(read_fd)
