"""Formulario de configuración real en un PTY; nunca modifica la red."""

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
from thinkisp.ui import setup
from thinkisp.ui.widgets import TerminalUI
from thinkisp.core.profile import Capabilities, Channel
fd = int(os.environ['THINKISP_TEST_EVENTS'])
def report(**extra):
    os.write(fd, (json.dumps({'kind': 'screen', **extra}) + '\n').encode())
caps = Capabilities('fixture-wifi', 'phy7', 'fixture-driver',
                    [Channel(6, 2437, 20), Channel(44, 5220, 17)],
                    widths=['auto','20','40','80'],
                    widths_by_band={'bg':['auto','20','40'],'a':['auto','20','40','80']},
                    optional={'802-11-wireless.channel-width':True})
setup.load_configuration = lambda: (caps, None, dict(setup.BASE_FIELDS, **{'802-11-wireless.ssid':'Mi red'}), None)
def save(settings, caps, power):
    report(title='saved', ssid=settings['802-11-wireless.ssid'],
           password_ok=settings['802-11-wireless-security.psk']=='PTYPasswordNeverEcho',
           band=settings['802-11-wireless.band'], channel=settings['802-11-wireless.channel'])
    return 'fixture-uuid'
setup.persist_configuration = save
original_frame = TerminalUI.frame
last = None
def frame(self, title, subtitle='', footer=''):
    global last
    result = original_frame(self, title, subtitle, footer)
    marker = (title, self.screen.getmaxyx())
    if marker != last:
        report(title=title, width=marker[1][1])
        last = marker
    return result
TerminalUI.frame = frame
original_key = TerminalUI.key
def key(self):
    preview = os.environ.get('THINKISP_TEST_PREVIEW')
    if preview and last and last[0].startswith('Crear tu red'):
        from pathlib import Path
        rows, cols = self.screen.getmaxyx()
        Path(preview).write_text('\n'.join(self.screen.instr(y,0).decode('utf-8','replace') for y in range(rows)))
    return original_key(self)
TerminalUI.key = key
original_edit = setup.SetupForm.edit_field
def edit(self, *args):
    original_edit(self, *args)
    report(title='edited', field=args[1])
setup.SetupForm.edit_field = edit
setup.configure_hotspot()
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


def send(keys):
    os.write(master, keys)


try:
    wait_event('screen', title='Crear tu red', width=120)
    send(b'\r')
    wait_event('screen', title='Nombre de la red')
    send('\x15Red portátil\r'.encode())
    wait_event('screen', title='edited', field='802-11-wireless.ssid')
    send(b'\x1bOB\r')
    wait_event('screen', title='Contraseña')
    send(b'PTYPasswordNeverEcho\r')
    wait_event('screen', title='Confirmar contraseña')
    send(b'PTYPasswordNeverEcho\r')
    wait_event('screen', title='edited', field='802-11-wireless-security.psk')
    send(b'\t\r')
    wait_event('screen', title='Banda')
    send(b'\x1bOB\r')
    wait_event('screen', title='edited', field='802-11-wireless.band')
    send(b'\x1bOB\r')
    wait_event('screen', title='Canal')
    send(b'\x1bOB\r')
    wait_event('screen', title='edited', field='802-11-wireless.channel')
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
    os.kill(process.pid, signal.SIGWINCH)
    wait_event('screen', title='Crear tu red · Sin guardar', width=80)
    send(b'\x1bOQ')
    wait_event('screen', title='Guardar configuración')
    send(b'\r')
    event = wait_event('screen', title='saved')
    assert event['ssid'] == 'Red portátil' and event['password_ok'], event
    assert event['band'] == 'a' and event['channel'] == '44', event
    process.wait(timeout=5)
    while select.select([master], [], [], 0.1)[0]:
        try:
            chunk = os.read(master, 65536)
        except OSError:
            break
        if not chunk:
            break
        output.extend(chunk)
    assert process.returncode == 0, process.returncode
    assert b'PTYPasswordNeverEcho' not in output, 'Se imprimió la contraseña'
    assert b'Traceback' not in output and b'thinkISP' in output
    print('PASS: configuración curses real, SSID Unicode, contraseña oculta, flechas, pestañas, 5 GHz, resize y guardado; red sin modificar.')

finally:
    if process.poll() is None:
        process.kill()
        process.wait(timeout=5)
    os.close(master)
    os.close(read_fd)
