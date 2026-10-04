"""Optional real Tk integration check. Set STUDIO_GUI_TEST=1 with a display."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('STUDIO_GUI_TEST') == '1', 'requires STUDIO_GUI_TEST=1 and a desktop display')
class GuiTests(unittest.TestCase):
    def test_merge_dialog_through_export_and_failed_merge_preserves_data(self):
        from irc_ibo_studio import app as module
        from test_merge import write_log
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);errors=[]
            f=write_log(root/'forward.log','forward',n=3)
            r=write_log(root/'reverse.log','reverse',n=2)
            with patch.object(module,'CONFIG',root/'settings.json'), \
                    patch.object(module.messagebox,'showerror',side_effect=lambda *a,**k:errors.append(a)):
                app=module.App()
                app.report_callback_exception=lambda typ,val,tb:errors.append(('Tk',str(val)))
                def wait():
                    deadline=time.monotonic()+5
                    while app.busy and time.monotonic()<deadline:
                        app.update();time.sleep(.01)
                    self.assertFalse(app.busy)
                try:
                    app.update();app.open_merge_dialog();app.update()
                    dialog=app.merge_dialog
                    dialog.forward.set(str(f));dialog.reverse.set(str(r));dialog.submit();wait();app.update()
                    self.assertEqual(len(app.display_frames),6)
                    self.assertTrue(app.align.get())
                    self.assertEqual(sum(x.branch=='ts' for x in app.selected()),1)
                    self.assertEqual(app.tree.item('2','tags'),('ts',))
                    app.stride.set('1')
                    xyz=root/'merged.xyz'
                    with patch.object(module.filedialog,'asksaveasfilename',return_value=str(xyz)):app.export_xyz()
                    self.assertTrue(xyz.is_file())
                    app.open_merge_dialog();app.update();dialog=app.merge_dialog
                    dialog.start_side.set('forward');dialog.submit();wait();app.update()
                    self.assertEqual(app.trajectory.frames[0].branch,'forward')
                    old=app.trajectory
                    app.open_merge_dialog();app.update();dialog=app.merge_dialog
                    dialog.reverse.set(str(f));dialog.submit();wait();app.update()
                    self.assertIs(app.trajectory,old)
                    self.assertEqual(len(errors),1)
                    self.assertIn('同じファイル',errors[0][1])
                finally:
                    app.stop_path_playback();app.stop_png_playback();app.destroy()

    def test_full_gui_workflow(self):
        from PIL import Image, ImageGrab
        from irc_ibo_studio import app as module
        from irc_ibo_studio.core import load_project
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            errors = []
            with patch.object(module, 'CONFIG', root/'settings.json'), \
                    patch.object(module.messagebox, 'showerror', side_effect=lambda *a, **k: errors.append(a)), \
                    patch.object(module.messagebox, 'showinfo'):
                app = module.App()
                app.report_callback_exception = lambda typ, val, tb: errors.append(('Tk callback', str(val)))
                try:
                    app.update()
                    self.assertEqual(len(app.tabs.tabs()),3)
                    self.assertFalse(hasattr(type(app),'export_jobs'))
                    app.read_path(module.BASE/'examples/synthetic_water_bend.xyz')
                    app.update()
                    self.assertEqual(len(app.display_frames), 71)
                    self.assertEqual(len(app.selected()), 8)
                    app.slider.set(70); app.update()
                    self.assertEqual(app.index, 70)
                    app.tree.selection_set('10'); app.update()
                    self.assertEqual(app.index, 10)
                    app.align.set(True); app.refresh_alignment(); app.update()
                    self.assertEqual(len(app.display_frames), 71)
                    app.toggle_play(); app.update(); app.toggle_play()
                    self.assertIsNone(app.play_job)
                    app.toggle_play(); app.update(); app.toggle_play()
                    self.assertIsNone(app.play_job)

                    xyz = root/'selection.xyz'
                    with patch.object(module.filedialog, 'asksaveasfilename', return_value=str(xyz)):
                        app.export_xyz()
                    self.assertTrue(xyz.is_file())
                    project=root/'project.json'
                    with patch.object(module.filedialog, 'asksaveasfilename', return_value=str(project)):
                        app.save_project()
                    self.assertEqual(len(load_project(project)[0].frames), 71)
                    with patch.object(module.filedialog, 'askopenfilename', return_value=str(project)):
                        app.open_project()
                    app.update()

                    folder=root/'png-a'; folder.mkdir()
                    app.png_dir.set(str(folder)); app.copy_script()
                    self.assertTrue((folder/'export_iboview_frames.js').is_file())
                    self.assertIn('doc.num_frames()', app.clipboard_get())
                    for i, color in enumerate(('red', 'green', 'blue')):
                        Image.new('RGB', (64,48), color).save(folder/f'frame-{i}.png')
                    app.read_pngs(folder); app.update()
                    app.toggle_png(); app.toggle_png()
                    self.assertIsNone(app.png_job)
                    other=root/'png-b'; other.mkdir()
                    Image.new('RGB',(64,48),'black').save(other/'other.png')
                    app.png_dir.set(str(other)); app.update()
                    self.assertEqual(app.preview_images, [])
                    app.kind.set('gif')
                    movie=root/'output.gif'
                    with patch.object(module.filedialog,'asksaveasfilename',return_value=str(movie)):
                        app.make_movie()
                    deadline=time.monotonic()+10
                    while app.busy and time.monotonic()<deadline:
                        app.update(); time.sleep(.01)
                    self.assertFalse(app.busy)
                    self.assertTrue(movie.is_file())
                    self.assertEqual(app.preview_images[0].parent, other)

                    results=[]
                    app.run_worker(lambda:42,results.append)
                    deadline=time.monotonic()+3
                    while app.busy and time.monotonic()<deadline:
                        app.update(); time.sleep(.01)
                    self.assertEqual(results,[42])
                    self.assertFalse(app.busy)
                    self.assertEqual(str(app.cancel_button['state']),'disabled')
                    self.assertLessEqual(app.cancel_button.winfo_rooty()+app.cancel_button.winfo_height(), app.winfo_rooty()+app.winfo_height())
                    self.assertEqual(errors, [])
                    app.tabs.select(app.path_tab);app.update()
                    self.assertLessEqual(app.xyz_button.winfo_rooty()+app.xyz_button.winfo_height(),app.path_tab.winfo_rooty()+app.path_tab.winfo_height())
                    screenshots=os.environ.get('STUDIO_SCREENSHOTS')
                    if screenshots:
                        target=Path(screenshots); target.mkdir(parents=True,exist_ok=True)
                        for i, tab in enumerate((app.path_tab,app.ibo_tab,app.movie_tab)):
                            app.tabs.select(tab); app.update()
                            screen=ImageGrab.grab(xdisplay=os.environ.get('DISPLAY'))
                            x,y=app.winfo_rootx(),app.winfo_rooty()
                            screen.crop((x,y,x+app.winfo_width(),y+app.winfo_height())).save(target/f'gui-{i+1}.png')
                finally:
                    app.stop_path_playback();app.stop_png_playback();app.destroy()


if __name__=='__main__':unittest.main()
