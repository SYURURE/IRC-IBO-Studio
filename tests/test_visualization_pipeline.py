"""Real data/output regression; external IboView and Tk are explicitly mocked."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import numpy as np
from PIL import Image
from irc_ibo_studio import app as module
from irc_ibo_studio.core import load_trajectory, save_project, load_project
from irc_ibo_studio.media import export_movie
from test_viewer_controls import harness, Harness, Value

for name in ('selected','_copy_selected_xyz','copy_xyz','launch_ibo','copy_script',
             'project_settings','open_project','read_pngs','stop_png_playback'):
    setattr(Harness,name,getattr(module.App,name))

def pipeline():
    h=harness()
    for name,value in dict(ibo_exe='',ffmpeg='',analysis_level='',png_dir='',stride='10',
                           fps='12',kind='gif',pingpong=True,png_info='').items():
        setattr(h,name,Value(value))
    h.clipboard_clear=Mock();h.clipboard_append=Mock();h.update=Mock()
    h.store_settings=Mock();h.busy=False;h.png_job=None;h.png_playing=False
    h.png_slider=Mock();h.draw_png=Mock();h.movie_tab='movie'
    return h

class VisualizationPipelineTests(unittest.TestCase):
    def test_old_project_ignores_removed_settings_and_preserves_source_metadata(self):
        h=pipeline()
        t=load_trajectory(module.BASE/'examples/synthetic_water_bend.xyz')
        with TemporaryDirectory() as d, patch.object(module.ImageTk,'PhotoImage',return_value=object()):
            p=Path(d)/'v022.json'
            save_project(t,p,dict(formchk='old.exe',method='old',basis='old',charge='7',multiplicity='9',
                                  nproc='4',memory_gb='3',start='2',end='12',stride='3',analysis_level='PBE / def2-SVP'))
            with patch.object(module.filedialog,'askopenfilename',return_value=str(p)):h.open_project()
            self.assertEqual(h.trajectory,t)
            self.assertEqual([f.point for f in h.selected()],[2,5,8,11,12])
            settings=h.project_settings()
            self.assertEqual(settings['analysis_level'],'PBE / def2-SVP')
            self.assertFalse({'formchk','method','basis','charge','multiplicity','nproc','memory_gb'} & settings.keys())
            save_project(h.trajectory,p,settings)
            self.assertEqual(load_project(p)[0],t)

    def test_xyz_clipboard_and_launch_preserve_atom_order_and_selected_coordinates(self):
        h=pipeline();h.stride.set('1')
        with TemporaryDirectory() as d:
            h.copy_xyz();text=h.clipboard_append.call_args.args[0]
            p=Path(d)/'clipboard.xyz';p.write_text(text)
            imported=load_trajectory(p)
            self.assertEqual(len(imported.frames),2)
            for src,dst in zip(h.selected(),imported.frames):
                self.assertEqual(src.symbols,dst.symbols)
                np.testing.assert_allclose(src.coords,dst.coords,atol=1e-10,rtol=0)
            exe=Path(d)/'Ibo View.exe';exe.touch();h.ibo_exe.set(str(exe))
            with patch.object(module.subprocess,'Popen') as launch:h.launch_ibo()
            launch.assert_called_once_with([str(exe.resolve())],cwd=str(exe.parent.resolve()))
            h.store_settings.assert_called_once()

    def test_script_to_png_import_to_real_gif_without_removed_tab(self):
        h=pipeline()
        with TemporaryDirectory() as d:
            folder=Path(d)/'画像';h.png_dir.set(str(folder));h.copy_script()
            script=(folder/'export_iboview_frames.js').read_text()
            self.assertIn('doc.num_frames()',script);self.assertIn('view.save_png',script)
            # Fixture PNGs represent the return boundary from the external app.
            for i,color in ((10,'blue'),(2,'green'),(1,'red')):
                Image.new('RGB',(80,60),color).save(folder/f'ibo-frame-{i}.png')
            h.read_pngs(folder)
            self.assertEqual([p.name for p in h.preview_images],['ibo-frame-1.png','ibo-frame-2.png','ibo-frame-10.png'])
            h.tabs.select.assert_called_with('movie')
            out=Path(d)/'movie.gif';export_movie(folder,out,fps=12,pingpong=False,kind='gif')
            with Image.open(out) as im:self.assertEqual(im.n_frames,3)
            with self.assertRaisesRegex(ValueError,'PNGがすでに'):h.copy_script()
            with self.assertRaisesRegex(ValueError,'フォルダー'):h.read_pngs('')
