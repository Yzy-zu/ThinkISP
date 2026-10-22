"""Paleta semántica compartida por dashboard, formularios y diálogos."""
import curses
import os


def enabled():
    mode = os.environ.get('THINKISP_COLOR', 'always').lower()
    if mode == 'never' or (mode == 'auto' and 'NO_COLOR' in os.environ):
        return False
    try:
        return curses.has_colors()
    except curses.error:
        return False


def initialize():
    if not enabled():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        default = -1
    except curses.error:
        default = curses.COLOR_BLACK
    rich = getattr(curses, 'COLORS', 0) >= 256
    bg, panel = (234, 235) if rich else (default, default)
    ink, muted, border = (252, 245, 60) if rich else (curses.COLOR_WHITE, curses.COLOR_WHITE, curses.COLOR_BLUE)
    cyan, violet, blue = (81, 141, 75) if rich else (curses.COLOR_CYAN, curses.COLOR_MAGENTA, curses.COLOR_BLUE)
    green, red, amber = (114, 203, 221) if rich else (curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_YELLOW)
    pairs = {
        1:(green,panel), 2:(red,panel), 3:(amber,panel), 4:(cyan,panel),
        5:(violet,panel), 6:(blue,panel), 7:(ink,panel), 8:(curses.COLOR_BLACK,cyan),
        9:(ink,panel), 10:(curses.COLOR_BLACK,green), 11:(curses.COLOR_BLACK,red),
        12:(curses.COLOR_BLACK,amber), 13:(ink,panel), 14:(ink,237 if rich else curses.COLOR_BLUE),
        15:(curses.COLOR_BLACK,curses.COLOR_WHITE), 16:(muted,panel), 17:(border,panel),
        18:(ink,bg), 19:(curses.COLOR_BLACK,violet), 20:(cyan,bg),
        21:(ink,236 if rich else default), 22:(violet,bg), 23:(muted,bg),
    }
    for number, (fg, background) in pairs.items():
        if number < getattr(curses, 'COLOR_PAIRS', 64):
            try:
                curses.init_pair(number, fg, background)
            except curses.error:
                pass


def color(number):
    if not enabled():
        return 0
    if number >= getattr(curses, 'COLOR_PAIRS', 64):
        number = 7 if number != 8 else 0
    return curses.color_pair(number)


def selected():
    value = color(8)
    return (value if value else curses.A_REVERSE) | curses.A_BOLD


def background(win):
    if hasattr(win, 'bkgd'):
        win.bkgd(' ', color(18))
