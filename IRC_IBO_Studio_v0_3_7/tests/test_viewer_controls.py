"""Viewer controller tests without a display; Tk rendering is mocked explicitly."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from irc_ibo_studio import app as module
from irc_ibo_studio.app import App
from irc_ibo_studio.core import Frame, Trajectory
from irc_ibo_studio.viewer import SceneRenderer, trajectory_extent


class Value:
    def __init__(self,value):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value


class Harness(SimpleNamespace):
    """Bind real handlers to lightweight stand-ins, without creating Tk()."""
    def guard(self,callback):return callback()


for name in ('begin_scene_drag','rotate','end_scene_drag','select_atom_at',
             'clear_picks','update_measurement','scale_view','wheel','reset_view',
             'restore_camera','request_scene_draw','draw_scene','save_scene_png',
             'stop_path_playback','set_frame','install_trajectory','refresh_alignment'):
    setattr(Harness,name,getattr(App,name))
Harness._short=staticmethod(App._short)


def event(x=0,y=0,state=0,delta=0):
    return SimpleNamespace(x=x,y=y,state=state,delta=delta)


def harness():
    frames=[Frame(['C','H','O'],[[0.,0.,0.],[1.,0.,0.],[0.,1.4,0.]],0,energy=-1.),
            Frame(['C','H','O'],[[0.,0.,0.],[1.2,0.,0.],[0.,1.5,0.]],1,energy=-1.1)]
    scene=Mock();scene.winfo_width.return_value=640;scene.winfo_height.return_value=320
    tree=Mock();tree.exists.return_value=False;tree.get_children.return_value=()
    h=Harness(display_frames=frames,trajectory=Trajectory(frames,'test.xyz'),
              index=0,yaw=-.45,pitch=.65,zoom=1.,pan=(0.,0.),drag=None,
              picked=[],scene=scene,scene_job=None,scene_image=None,
              scene_projection=None,scene_photo=None,scene_extent=trajectory_extent(frames),
              scene_renderer=SceneRenderer(),labels=Value(False),show_bonds=Value(True),
              measurement=Value(''),clear_measurement_button=Mock(),
              status=Value(''),after_idle=Mock(return_value='pending'),after_cancel=Mock(),
              play_btn=Mock(),play_job=None,playing=False,slider=Mock(),tree=tree,
              frame_label=Value(''),draw_plot=Mock(),align=Value(False),
              ibo_metadata=Value(''),start=Value('0'),end=Value('1'),
              info=Value(''),tabs=Mock(),path_tab='path')
    return h


class ViewerControllerTests(unittest.TestCase):
    def setUp(self):
        self.photo_patch=patch.object(module.ImageTk,'PhotoImage',side_effect=lambda image:object())
        self.photo_patch.start();self.addCleanup(self.photo_patch.stop)

    def test_rotate_and_pan_leave_scientific_data_unchanged(self):
        h=harness();original=deepcopy(h.trajectory)
        h.begin_scene_drag(event(10,20));h.rotate(event(30,40));h.end_scene_drag(event(30,40))
        self.assertAlmostEqual(h.yaw,-.25);self.assertAlmostEqual(h.pitch,.85)
        self.assertEqual(h.pan,(0.,0.));self.assertEqual(h.picked,[])
        h.begin_scene_drag(event(20,20,1));h.rotate(event(35,8,1));h.end_scene_drag(event(35,8))
        self.assertEqual(h.pan,(15.,-12.));self.assertAlmostEqual(h.yaw,-.25)
        self.assertEqual(h.trajectory,original)

    def test_mouse_jitter_clicks_but_drag_does_not(self):
        h=harness();h.select_atom_at=Mock()
        h.begin_scene_drag(event(10,10));h.rotate(event(12,11));h.end_scene_drag(event(12,11))
        h.select_atom_at.assert_called_once_with(12,11)
        h.select_atom_at.reset_mock()
        h.begin_scene_drag(event(10,10));h.rotate(event(20,20));h.rotate(event(10,10));h.end_scene_drag(event(10,10))
        h.select_atom_at.assert_not_called()

    def test_shift_click_never_becomes_distance_selection(self):
        h=harness();h.select_atom_at=Mock()
        h.begin_scene_drag(event(10,10,1));h.end_scene_drag(event(10,10,0))
        h.begin_scene_drag(event(10,10,0));h.end_scene_drag(event(10,10,1))
        h.select_atom_at.assert_not_called()

    def test_wheel_uses_reference_factor_and_clamps(self):
        h=harness();h.wheel(event(delta=120));self.assertAlmostEqual(h.zoom,1.12)
        h.wheel(event(delta=-120));self.assertAlmostEqual(h.zoom,1.)
        h.scale_view(1000);self.assertEqual(h.zoom,8.)
        h.scale_view(.0001);self.assertEqual(h.zoom,.15)
        h.reset_view();self.assertEqual((h.yaw,h.pitch,h.zoom,h.pan),(-.45,.65,1.,(0.,0.)))

    def test_picks_toggle_restart_and_clear(self):
        h=harness();h.draw_scene()
        with patch.object(module,'hit_test',side_effect=[0,1,2,2,None]):
            h.select_atom_at(0,0);self.assertEqual(h.picked,[0])
            h.select_atom_at(0,0);self.assertEqual(h.picked,[0,1])
            h.select_atom_at(0,0);self.assertEqual(h.picked,[2])
            h.select_atom_at(0,0);self.assertEqual(h.picked,[])
            h.picked=[0];h.select_atom_at(0,0);self.assertEqual(h.picked,[])

    def test_selected_distance_tracks_frame_and_has_badge(self):
        h=harness();h.picked=[0,1];h.draw_scene()
        self.assertIn('1 C - 2 H: 1.0000 Å',h.measurement.get())
        self.assertEqual(h.scene_image.getpixel((10,290)),(242,247,250))
        h.set_frame(1);h.draw_scene()
        self.assertEqual(h.picked,[0,1]);self.assertIn('1.2000 Å',h.measurement.get())
        self.assertEqual(h.scene_image.size,(640,320))

    def test_new_trajectory_clears_picks(self):
        h=harness();h.picked=[0,1]
        replacement=Trajectory(deepcopy(h.display_frames),'replacement.xyz')
        h.install_trajectory(replacement)
        self.assertEqual(h.picked,[])
        self.assertEqual(h.measurement.get(),'距離：原子を2つクリック')

    def test_repeated_requests_coalesce_and_render_releases_job(self):
        h=harness();h.request_scene_draw();h.request_scene_draw();h.request_scene_draw()
        h.after_idle.assert_called_once();self.assertEqual(h.scene_job,'pending')
        h.draw_scene();h.after_cancel.assert_called_once_with('pending')
        self.assertIsNone(h.scene_job)
        self.assertEqual(h.scene.create_image.call_count,1)

    def test_png_snapshots_current_raster_before_native_dialog(self):
        h=harness();h.picked=[0,1];h.playing=True;h.draw_scene()
        original=np.asarray(h.scene_image).copy()
        with TemporaryDirectory() as directory:
            path=Path(directory)/'preview.png'
            def choose(**kwargs):
                self.assertTrue(kwargs['confirmoverwrite'])
                h.scene_image.paste('red',(0,0,640,320));h.index=1
                return str(path)
            with patch.object(module.filedialog,'asksaveasfilename',side_effect=choose):
                h.save_scene_png()
            with Image.open(path) as actual:
                np.testing.assert_array_equal(np.asarray(actual),original)
        self.assertFalse(h.playing);self.assertIn('位置 0',h.status.get())

    def test_png_cancel_writes_nothing_and_missing_path_is_rejected(self):
        h=harness()
        with patch.object(module.filedialog,'asksaveasfilename',return_value=''),patch.object(Image.Image,'save') as save:
            h.save_scene_png();save.assert_not_called()
        h.display_frames=[]
        with self.assertRaisesRegex(ValueError,'IRCログ'):
            h.save_scene_png()

    def test_camera_restore_accepts_old_project_and_sanitizes_nonfinite(self):
        h=harness();h.restore_camera({'yaw':.2,'pitch':.3,'zoom':2})
        self.assertEqual((h.yaw,h.pitch,h.zoom,h.pan),(.2,.3,2.,(0.,0.)))
        h.restore_camera({'yaw':float('nan'),'pitch':'bad','zoom':900,'pan_x':float('inf'),'pan_y':'-4'})
        self.assertEqual((h.yaw,h.pitch,h.zoom,h.pan),(-.45,.65,8.,(0.,-4.)))

    def test_oversized_viewport_reports_safely_and_cannot_save_stale_image(self):
        h=harness();h.draw_scene();self.assertIsNotNone(h.scene_image)
        h.scene.winfo_width.return_value=5000
        h.draw_scene()
        self.assertIsNone(h.scene_image);self.assertIsNone(h.scene_photo)
        self.assertIsNone(h.scene_projection);self.assertIn('構造プレビュー',h.status.get())
        self.assertIn('ウィンドウを小さく',h.scene.create_text.call_args.kwargs['text'])
        with self.assertRaisesRegex(ValueError,'描画できません'):
            h.save_scene_png()
        h.scene.winfo_width.return_value=640;h.draw_scene()
        self.assertIsNotNone(h.scene_image)


if __name__=='__main__':unittest.main()
