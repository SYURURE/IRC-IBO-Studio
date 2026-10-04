"""Regression tests for completion state and bounded responsive layout."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from studio_launcher import set_progress_state
from irc_ibo_studio.app import StableColumns, column_layout


class StabilityTests(unittest.TestCase):
    def test_success_bar_stays_full_and_retry_restores_animation(self):
        bar=Mock()
        set_progress_state(bar,'done')
        bar.stop.assert_called_once()
        bar.configure.assert_called_once_with(mode='determinate',maximum=100,value=100)
        bar.start.assert_not_called()
        bar.reset_mock();set_progress_state(bar,'running')
        bar.configure.assert_called_once_with(mode='indeterminate',maximum=100,value=0)
        bar.start.assert_called_once()

    def test_failure_does_not_show_success(self):
        bar=Mock();set_progress_state(bar,'error')
        bar.configure.assert_called_once_with(mode='determinate',maximum=100,value=0)
        bar.start.assert_not_called()

    def test_column_widths_do_not_change_with_wrapped_text_heights(self):
        for width in (700,939,940,1100,1500):
            a=column_layout(width,800,500);b=column_layout(width,480,620)
            self.assertEqual(a[0][2],b[0][2]);self.assertEqual(a[1][2],b[1][2])
            for x,y,w in b[:2]:
                self.assertGreater(w,0);self.assertLessEqual(x+w,width)
            if width<940:
                self.assertEqual(b[1][1],492);self.assertEqual(b[2],1112)
            else:
                self.assertEqual(b[2],620);self.assertEqual(b[1][1],0)

    def test_height_only_configure_does_not_restart_width_layout(self):
        obj=SimpleNamespace(last_width=1000,_schedule=Mock())
        for height in (300,400,350,350):
            StableColumns._resize(obj,SimpleNamespace(width=1000,height=height))
        obj._schedule.assert_not_called()
        StableColumns._resize(obj,SimpleNamespace(width=800,height=350))
        obj._schedule.assert_called_once()

    def test_repeated_identical_layout_stops_mutating_widgets(self):
        left=Mock();right=Mock()
        left.winfo_reqheight.return_value=400;right.winfo_reqheight.return_value=500
        obj=SimpleNamespace(pending='job',last_layout=None,left=left,right=right,
                            winfo_width=lambda:1100,cget=lambda key:500,configure=Mock())
        StableColumns._layout(obj)
        left.place.assert_called_once_with(x=0,y=0,width=544)
        right.place.assert_called_once_with(x=556,y=0,width=544)
        for _ in range(20):StableColumns._layout(obj)
        self.assertEqual(left.place.call_count,1);self.assertEqual(right.place.call_count,1)
        obj.configure.assert_not_called()

    def test_configure_burst_is_coalesced(self):
        obj=SimpleNamespace(pending=None,after_idle=Mock(return_value='job'),_layout=Mock())
        for _ in range(20):StableColumns._schedule(obj)
        obj.after_idle.assert_called_once_with(obj._layout)
