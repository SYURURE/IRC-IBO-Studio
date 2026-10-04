from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, Mock
import json
import sys
import unittest
import studio_launcher as launcher
from setup_process import SetupControl, SetupCancelled


class LauncherTests(unittest.TestCase):
    def tree(self, root):
        (root/'irc_ibo_studio').mkdir()
        for name in ('run_app.py','irc_ibo_studio/app.py'):
            (root/name).write_text('')
        (root/'requirements.txt').write_text('numpy>=1.26,<3\nPillow>=10,<13\nimageio-ffmpeg>=0.5,<1\n')

    def runner(self,args,log,cwd):
        if args[1:3] == ['-m','venv']:
            candidate=Path(args[-1])
            python=launcher.venv_python(candidate)
            python.parent.mkdir(parents=True,exist_ok=True);python.touch()
            launcher.venv_python(candidate,True).touch()
        elif 'install' in args:
            # Represent packages and their transitive dependencies in the candidate.
            python=Path(args[0]);env=python.parent.parent
            (env/('package-'+str(args[-1]).split('>')[0])).mkdir(exist_ok=True)
            (env/'transitive-dependency').mkdir(exist_ok=True)

    def test_setup_marks_success_after_validation_and_works_with_unicode_spaces(self):
        with TemporaryDirectory(prefix='studio テスト ') as d:
            root=Path(d);self.tree(root);progress=[];statuses=[];run=Mock(side_effect=self.runner)
            self.assertFalse(launcher.ready(root))
            launcher.prepare(progress.append,root,run,module_update=lambda *item:statuses.append(item))
            self.assertTrue(launcher.ready(root));self.assertEqual(run.call_count,5)
            self.assertIn('Python仮想環境を構築',progress[0])
            for name,_ in launcher.required_modules(root):
                self.assertEqual([s for n,s in statuses if n==name],['未インストール','インストール中','インストール終了'])
            self.assertFalse((root/'.studio-setup.lock').exists())
            (root/'requirements.txt').write_text('Pillow')
            self.assertFalse(launcher.ready(root))

    def test_failed_repair_preserves_previous_environment_and_marker(self):
        with TemporaryDirectory() as d:
            root=Path(d);self.tree(root);launcher.prepare(lambda _:None,root,self.runner)
            marker=(root/'.studio-setup.json').read_bytes();python=launcher.environment_python(root)
            previous=set((root/'.studio-environments').iterdir())
            with self.assertRaisesRegex(RuntimeError,'network'):
                launcher.prepare(lambda _:None,root,Mock(side_effect=RuntimeError('network')))
            self.assertTrue(launcher.ready(root));self.assertTrue(python.exists())
            self.assertEqual(marker,(root/'.studio-setup.json').read_bytes())
            self.assertEqual(previous,set((root/'.studio-environments').iterdir()))
            self.assertFalse((root/'.studio-setup.lock').exists())

    def test_cancel_at_each_install_stage_removes_only_new_environment(self):
        for cancel_call in range(1,6):
            with self.subTest(cancel_call=cancel_call),TemporaryDirectory() as d:
                root=Path(d);self.tree(root);launcher.prepare(lambda _:None,root,self.runner)
                previous=set((root/'.studio-environments').iterdir())
                original=(root/'.studio-setup.json').read_bytes()
                unrelated=root/'input.xyz';unrelated.write_text('user input')
                control=SetupControl();calls=[];states=[]
                def run(args,log,cwd):
                    self.runner(args,log,cwd);calls.append(args)
                    if len(calls)==cancel_call:control.cancel()
                with self.assertRaises(SetupCancelled):
                    launcher.prepare(lambda _:None,root,run,control,lambda *x:states.append(x))
                self.assertEqual(previous,set((root/'.studio-environments').iterdir()))
                self.assertEqual(original,(root/'.studio-setup.json').read_bytes())
                self.assertEqual(unrelated.read_text(),'user input')
                self.assertTrue(launcher.ready(root))
                self.assertTrue(all(s=='未インストール' for n,s in states[-3:]))
                self.assertFalse((root/'.studio-setup.lock').exists())

    def test_cleanup_failure_is_reported_not_claimed_as_success(self):
        with TemporaryDirectory() as d:
            root=Path(d);self.tree(root)
            with patch.object(launcher.shutil,'rmtree',side_effect=PermissionError('locked')):
                with self.assertRaisesRegex(RuntimeError,'削除が完了していません'):
                    launcher.prepare(lambda _:None,root,Mock(side_effect=SetupCancelled()))
            self.assertFalse(launcher.ready(root))
            self.assertTrue(list((root/'.studio-environments').iterdir()))

    def test_concurrent_setup_does_not_delete_other_process_lock(self):
        with TemporaryDirectory() as d:
            root=Path(d);self.tree(root);lock=root/'.studio-setup.lock';lock.touch()
            with self.assertRaisesRegex(RuntimeError,'別のセットアップ'):
                launcher.prepare(lambda _:None,root,self.runner)
            self.assertTrue(lock.exists())

    def test_launch_uses_isolated_interpreter_and_absolute_paths_without_shell(self):
        with TemporaryDirectory(prefix='studio space ') as d:
            root=Path(d);self.tree(root)
            with self.assertRaisesRegex(RuntimeError,'01_setup'):launcher.start_application(root)
            launcher.prepare(lambda _:None,root,self.runner)
            with patch.object(launcher.subprocess,'Popen') as popen:
                launcher.start_application(root)
            args,kw=popen.call_args
            self.assertEqual(args[0],[str(launcher.environment_python(root,True)),str(root/'run_app.py')])
            self.assertEqual(kw['cwd'],str(root));self.assertNotIn('shell',kw)

    def test_legacy_venv_still_launches(self):
        with TemporaryDirectory() as d:
            root=Path(d);self.tree(root)
            self.runner([sys.executable,'-m','venv',root/'.venv'],None,root)
            (root/'.studio-setup.json').write_text(json.dumps({'directory':str(root.resolve()),'requirements':launcher.fingerprint(root)}))
            self.assertTrue(launcher.ready(root))
            self.assertEqual(launcher.environment_python(root),launcher.venv_python(root/'.venv'))

    def test_failed_command_propagates_exit_code(self):
        with TemporaryDirectory() as d,(Path(d)/'log').open('w') as log:
            with self.assertRaisesRegex(RuntimeError,'2'):
                launcher.run_command([sys.executable,'-c','raise SystemExit(2)'],log,Path(d))

    def test_invalid_requirement_cannot_supply_pip_options(self):
        with TemporaryDirectory() as d:
            root=Path(d);self.tree(root);(root/'requirements.txt').write_text('--target=elsewhere')
            with self.assertRaisesRegex(RuntimeError,'対応していない'):launcher.required_modules(root)
