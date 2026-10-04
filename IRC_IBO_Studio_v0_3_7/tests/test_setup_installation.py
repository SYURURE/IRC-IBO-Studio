"""Offline real venv/pip transaction checks using a tiny local test wheel."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import unittest
import zipfile
import studio_launcher as launcher
from setup_process import SetupControl, SetupCancelled


class InstallationTests(unittest.TestCase):
    def fixture(self, root):
        (root/'irc_ibo_studio').mkdir()
        (root/'irc_ibo_studio/app.py').touch()
        (root/'requirements.txt').write_text('studio-setup-probe==1.0\n')
        (root/'run_app.py').write_text("import setup_probe\nfrom pathlib import Path\nPath('launched').write_text(str(setup_probe.VALUE))\n")
        wheel=root/'studio_setup_probe-1.0-py3-none-any.whl'
        with zipfile.ZipFile(wheel,'w') as archive:
            files={'setup_probe.py':'VALUE = 123\n',
                   'studio_setup_probe-1.0.dist-info/METADATA':'Metadata-Version: 2.1\nName: studio-setup-probe\nVersion: 1.0\n',
                   'studio_setup_probe-1.0.dist-info/WHEEL':'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
            record='studio_setup_probe-1.0.dist-info/RECORD'
            files[record]=''.join(name+',,\n' for name in list(files)+[record])
            for name,data in files.items():archive.writestr(name,data)
        return wheel

    def setup_runner(self, control, wheel):
        def run(args,log,cwd):
            args=list(args)
            if 'install' in args:
                args[-1:]=['--no-index',str(wheel)]
            elif '-c' in args:
                # Production tests the real Tk app. This fixture tests its own
                # installed module, without requiring a desktop or internet.
                args[-1]='import setup_probe; assert setup_probe.VALUE == 123'
            control.run(args,log,cwd)
        return run

    def test_installed_module_removed_on_cancel_old_environment_preserved(self):
        with TemporaryDirectory(prefix='studio 実インストール ') as d:
            root=Path(d);wheel=self.fixture(root);control=SetupControl()
            old=root/'.venv'/'keep.txt';old.parent.mkdir();old.write_text('previous environment')
            marker=root/'.studio-setup.json';original=json.dumps({'environment':'.venv'})
            marker.write_text(original)
            installed=[]
            def module_update(name,status):
                if status=='インストール終了':
                    installed.extend((root/'.studio-environments').rglob('setup_probe.py'))
                    self.assertTrue(installed,'pip must have really installed the test module')
                    control.pause();control.cancel()
            with self.assertRaises(SetupCancelled):
                launcher.prepare(lambda _:None,root,self.setup_runner(control,wheel),control,module_update)
            self.assertTrue(all(not path.exists() for path in installed))
            self.assertEqual(list((root/'.studio-environments').iterdir()),[])
            self.assertEqual(marker.read_text(),original)
            self.assertEqual(old.read_text(),'previous environment')

    def test_success_commits_environment_that_really_launches(self):
        with TemporaryDirectory(prefix='studio 完了 ') as d:
            root=Path(d);wheel=self.fixture(root);control=SetupControl()
            launcher.prepare(lambda _:None,root,self.setup_runner(control,wheel),control)
            self.assertTrue(launcher.ready(root))
            process=launcher.start_application(root)
            self.assertEqual(process.wait(timeout=10),0)
            self.assertEqual((root/'launched').read_text(),'123')
            self.assertFalse(control.pause())
