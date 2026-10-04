"""Headless checks for responsive layout arithmetic (not visual GUI tests)."""
import unittest

from irc_ibo_studio.app import App, action_layout


class LayoutArithmeticTests(unittest.TestCase):
    def test_single_row_keeps_spacing(self):
        positions,height=action_layout(900,[(140,32),(160,34),(180,32)])
        self.assertEqual(positions,[(0,0),(147,0),(314,0)])
        self.assertEqual(height,34)

    def test_wrapped_rows_have_independent_column_widths(self):
        sizes=[(155,34),(140,34),(190,34),(190,34)]
        positions,height=action_layout(500,sizes)
        self.assertEqual(positions,[(0,0),(162,0),(309,0),(0,37)])
        self.assertEqual(height,71)
        for (x,y),(width,_) in zip(positions,sizes):
            self.assertLessEqual(x+width,500)

    def test_large_fonts_wrap_without_overlap(self):
        sizes=[(310,55),(280,60),(380,55),(380,55)]
        positions,height=action_layout(930,sizes)
        self.assertEqual(positions,[(0,0),(317,0),(0,63),(387,63)])
        self.assertEqual(height,118)
        for (x,_),(width,_) in zip(positions,sizes):
            self.assertLessEqual(x+width,930)

    def test_empty_toolbar(self):
        self.assertEqual(action_layout(500,[]),([],0))

    def test_status_preview_is_bounded_without_changing_source(self):
        source='長いファイル名と診断情報'*30
        preview=App._short(source,180)
        self.assertEqual(len(preview),180)
        self.assertTrue(preview.endswith('…'))
        self.assertEqual(App._short('短い情報',180),'短い情報')
        self.assertGreater(len(source),len(preview))

    def test_pane_constraints_keep_both_sides_accessible(self):
        class Panes:
            def __init__(self):self.position=10;self.bindings={}
            def winfo_width(self):return 960
            def sashpos(self,index,value=None):
                if value is not None:self.position=value
                return self.position
            def bind(self,event,callback,add=None):self.bindings[event]=callback
        panes=Panes();App._limit_panes(panes,290,350)
        panes.bindings['<Configure>']()
        self.assertEqual(panes.position,290)
        panes.position=950;panes.bindings['<ButtonRelease-1>']()
        self.assertEqual(panes.position,610)


if __name__=='__main__':unittest.main()
