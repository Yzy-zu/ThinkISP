"""Contraste, degradación a 8 colores y modo sin color sin hardware real."""
from contextlib import ExitStack
import curses
import os
import unittest
from unittest.mock import patch
from thinkisp.ui import theme


class TestTheme(unittest.TestCase):
    def palette(self, colors):
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'THINKISP_COLOR':'always'}))
            stack.enter_context(patch.object(curses,'has_colors',return_value=True))
            stack.enter_context(patch.object(curses,'start_color'))
            stack.enter_context(patch.object(curses,'use_default_colors'))
            stack.enter_context(patch.object(curses,'COLORS',colors,create=True))
            stack.enter_context(patch.object(curses,'COLOR_PAIRS',64,create=True))
            calls=stack.enter_context(patch.object(curses,'init_pair'))
            theme.initialize()
            return {call.args[0]:call.args[1:] for call in calls.call_args_list}

    def test_rich_palette_has_dark_surfaces_and_distinct_accents(self):
        pairs=self.palette(256)
        self.assertEqual(pairs[18],(252,234))
        self.assertEqual(pairs[13],(252,235))
        self.assertNotEqual(pairs[4],pairs[5])
        self.assertEqual(pairs[8],(curses.COLOR_BLACK,81))
        self.assertEqual(pairs[15],(curses.COLOR_BLACK,curses.COLOR_WHITE))

    def test_eight_color_terminal_never_receives_extended_indexes(self):
        for fg,bg in self.palette(8).values():
            self.assertTrue(0 <= fg < 8)
            self.assertTrue(-1 <= bg < 8)

    def test_color_preferences_have_explicit_override_and_monochrome_fallback(self):
        with patch.object(curses,'has_colors',return_value=True):
            with patch.dict(os.environ,{'NO_COLOR':'1','THINKISP_COLOR':'always'}):
                self.assertTrue(theme.enabled())
            with patch.dict(os.environ,{'NO_COLOR':'1','THINKISP_COLOR':'auto'}):
                self.assertFalse(theme.enabled())
            with patch.dict(os.environ,{'THINKISP_COLOR':'never'}):
                self.assertEqual(theme.color(4),0)
                self.assertTrue(theme.selected() & curses.A_REVERSE)

    def test_non_initialized_curses_can_be_used_by_pure_render_tests(self):
        with patch.object(curses,'has_colors',side_effect=curses.error('not initialized')):
            self.assertEqual(theme.color(4),0)
