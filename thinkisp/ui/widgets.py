"""Controles de terminal compartidos; únicamente el hilo principal usa curses."""
import curses
from concurrent.futures import Future
import threading
import time
import textwrap
import unicodedata
from thinkisp.ui import theme


def clipped(value, width):
    result, cells = '', 0
    for char in str(value):
        if unicodedata.category(char).startswith('C'):
            char = ' '
        size = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
        if cells + size > max(0, width):
            break
        result += char
        cells += size
    return result


def put(win, y, x, text, style=0):
    h, w = win.getmaxyx()
    if 0 <= y < h and 0 <= x < w - 1:
        try:
            if not style & curses.A_COLOR and hasattr(win, 'inch'):
                style |= win.inch(y, x) & curses.A_COLOR
            win.addstr(y, x, clipped(text, w - x - 1), style)
        except curses.error:
            pass


def initialize(win):
    win.keypad(True)
    win.timeout(100)
    try:
        curses.curs_set(0)
        curses.set_escdelay(25)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
    except (AttributeError, curses.error):
        pass
    theme.initialize()
    theme.background(win)



def accent():
    return theme.color(4) if curses.has_colors() else curses.A_BOLD


class TerminalUI:
    def __init__(self, screen):
        self.screen = screen
        initialize(screen)

    def frame(self, title, subtitle='', footer='Esc Volver'):
        win = self.screen
        win.erase()
        h, w = win.getmaxyx()
        put(win, 0, 2, '◈ thinkISP', theme.color(22) | curses.A_BOLD)
        put(win, 0, 15, title, curses.A_BOLD | theme.color(4))
        put(win, 2, 3, subtitle, theme.color(16))
        put(win, h - 2, 2, footer, accent())
        return h, w

    def key(self):
        self.screen.refresh()
        try:
            return self.screen.get_wch()
        except curses.error:
            return None

    def choose(self, title, choices, current=None):
        if not choices:
            self.message(title, 'No hay opciones compatibles disponibles.')
            return None
        selected = next((i for i, choice in enumerate(choices) if choice[0] == current), 0)
        while True:
            h, w = self.frame(title, 'Selecciona con las flechas. Enter aplica la selección.', '↑↓ Elegir   Enter Aceptar   Esc Cancelar')
            size = max(1, h - 7)
            offset = max(0, min(selected - size + 1, len(choices) - size))
            for row, (_, label) in enumerate(choices[offset:offset + size], 4):
                index = offset + row - 4
                put(self.screen, row, 2, clipped((' › ' if index == selected else '   ') + label, w - 4).ljust(max(0, w - 5)),
                    theme.selected() if index == selected else theme.color(21 if index % 2 else 7))
            key = self.key()
            if key in ('\x1b', '\x03'):
                return None
            if key in (curses.KEY_UP, 'k'):
                selected = (selected - 1) % len(choices)
            elif key in (curses.KEY_DOWN, 'j', '\t'):
                selected = (selected + 1) % len(choices)
            elif key == curses.KEY_HOME:
                selected = 0
            elif key == curses.KEY_END:
                selected = len(choices) - 1
            elif key in ('\n', '\r', curses.KEY_ENTER):
                return choices[selected][0]
            elif key == curses.KEY_MOUSE:
                try:
                    _, _, my, _, flags = curses.getmouse()
                    if 4 <= my < 4 + min(size, len(choices)):
                        selected = offset + my - 4
                        if flags & curses.BUTTON1_DOUBLE_CLICKED:
                            return choices[selected][0]
                except curses.error:
                    pass

    def edit(self, title, value='', secret=False, hint=''):
        # Password fields start empty so an existing secret never reaches a frame.
        text = '' if secret else str(value)
        cursor = len(text)
        while True:
            h, w = self.frame(title, hint, '←→ Mover cursor   Ctrl+U Limpiar   Enter Aceptar   Esc Cancelar')
            shown = '•' * len(text) if secret else text
            capacity = max(1, w - 7)
            start = cursor
            while start and len(clipped(shown[start - 1:cursor], capacity - 1)) == len(shown[start - 1:cursor]):
                start -= 1
            put(self.screen, 5, 2, ' ' * max(0, w - 5), theme.color(14))
            put(self.screen, 5, 3, clipped(shown[start:], capacity), theme.color(14))
            put(self.screen, 7, 2, '● Contraseña oculta' if secret else 'Los cambios se aplican al guardar el formulario.', curses.A_DIM)
            try:
                from_wcwidth = sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 0 if unicodedata.combining(c) else 1 for c in shown[start:cursor])
                self.screen.move(5, min(w - 2, 3 + from_wcwidth))
                curses.curs_set(1)
            except curses.error:
                pass
            key = self.key()
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            if key in ('\x1b', '\x03'):
                return None
            if key in ('\n', '\r', curses.KEY_ENTER):
                return text
            if key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
                if cursor:
                    text, cursor = text[:cursor - 1] + text[cursor:], cursor - 1
            elif key == curses.KEY_DC:
                text = text[:cursor] + text[cursor + 1:]
            elif key == curses.KEY_LEFT:
                cursor = max(0, cursor - 1)
            elif key == curses.KEY_RIGHT:
                cursor = min(len(text), cursor + 1)
            elif key in (curses.KEY_HOME, '\x01'):
                cursor = 0
            elif key in (curses.KEY_END, '\x05'):
                cursor = len(text)
            elif key == '\x15':
                text, cursor = '', 0
            elif isinstance(key, str) and key.isprintable():
                text, cursor = text[:cursor] + key + text[cursor:], cursor + len(key)

    def message(self, title, text):
        original = str(text).splitlines() or ['']
        offset = 0
        while True:
            h, w = self.frame(title, 'Información del equipo', '↑↓ Desplazar   Enter / Esc Volver')
            lines = [part for line in original for part in (textwrap.wrap(line, max(1, w - 5)) or [''])]
            offset = min(offset, max(0, len(lines) - max(1, h - 7)))
            size = max(1, h - 7)
            for row, line in enumerate(lines[offset:offset + size], 4):
                put(self.screen, row, 2, line)
            key = self.key()
            if key in ('\x1b', '\x03', '\n', '\r', curses.KEY_ENTER):
                return
            if key in (curses.KEY_DOWN, curses.KEY_NPAGE):
                offset = min(max(0, len(lines) - size), offset + (size if key == curses.KEY_NPAGE else 1))
            elif key in (curses.KEY_UP, curses.KEY_PPAGE):
                offset = max(0, offset - (size if key == curses.KEY_PPAGE else 1))

    def work(self, title, operation):
        future = Future()
        def execute():
            try:
                future.set_result(operation())
            except BaseException as exc:
                future.set_exception(exc)
        threading.Thread(target=execute, daemon=True).start()
        started = time.monotonic()
        while not future.done():
            self.frame(title, 'Consultando el sistema…', 'Espera a que termine la operación.')
            put(self.screen, 5, 3, '◐◓◑◒'[int((time.monotonic() - started) * 8) % 4] + '  ' + title, accent())
            try:
                self.key()  # Resize/redraw stays responsive until the operation finishes.
            except KeyboardInterrupt:
                pass  # Do not abandon an in-flight NetworkManager write.
        return future.result()
