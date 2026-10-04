"""Real process-tree tests; runnable unchanged on Windows and POSIX."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch
from setup_process import SetupControl, SetupCancelled


def wait_until(predicate, timeout=8):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.025)
    raise AssertionError('Timed out waiting for subprocess')


def size(path):
    try:return path.stat().st_size
    except FileNotFoundError:return 0


class ProcessTests(unittest.TestCase):
    def start_worker(self,control,args,log,root):
        errors=[]
        def run():
            try:control.run(args,log,root)
            except Exception as exc:errors.append(exc)
        worker=threading.Thread(target=run);worker.start()
        return worker,errors

    def test_pause_resume_and_cancel_include_child_process(self):
        with TemporaryDirectory(prefix='setup 日本語 ') as d:
            root=Path(d);heartbeat=root/'child-heartbeat'
            child="import time,sys\nf=open(sys.argv[1],'ab',buffering=0)\nwhile True:\n f.write(b'x');time.sleep(.015)"
            parent="import subprocess,sys,time\np=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]])\np.wait()"
            with (root/'log').open('w') as log:
                control=SetupControl()
                worker,errors=self.start_worker(control,[sys.executable,'-c',parent,child,str(heartbeat)],log,root)
                try:
                    wait_until(lambda:size(heartbeat)>3)
                    self.assertTrue(control.pause());time.sleep(.10)
                    paused_size=size(heartbeat);time.sleep(.20)
                    self.assertEqual(size(heartbeat),paused_size)
                    control.resume();wait_until(lambda:size(heartbeat)>paused_size+3)
                    control.pause();control.cancel();worker.join(10)
                    self.assertFalse(worker.is_alive());self.assertEqual(len(errors),1)
                    self.assertIsInstance(errors[0],SetupCancelled)
                    ended=size(heartbeat);time.sleep(.10);self.assertEqual(size(heartbeat),ended)
                finally:
                    control.cancel();worker.join(10)

    def test_pause_between_commands_blocks_spawn_until_resume(self):
        with TemporaryDirectory() as d:
            root=Path(d);target=root/'started'
            with (root/'log').open('w') as log:
                control=SetupControl();control.pause()
                worker,errors=self.start_worker(control,[sys.executable,'-c',"from pathlib import Path;Path('started').touch()"],log,root)
                try:
                    time.sleep(.10);self.assertFalse(target.exists())
                    control.resume();worker.join(8)
                    self.assertFalse(worker.is_alive());self.assertEqual(errors,[]);self.assertTrue(target.exists())
                finally:control.cancel();worker.join(8)

    def test_cancel_before_spawn_never_starts_child(self):
        control=SetupControl();control.pause();control.cancel()
        with patch('setup_process.ProcessTree') as tree:
            with self.assertRaises(SetupCancelled):control.run(['unused'],Mock(),Path('.'))
            tree.assert_not_called()

    def test_commit_waits_for_resume_and_is_blocked_by_cancel(self):
        control=SetupControl();control.pause();callback=Mock();errors=[]
        def commit():
            try:control.commit(callback)
            except Exception as exc:errors.append(exc)
        worker=threading.Thread(target=commit);worker.start()
        time.sleep(.05);callback.assert_not_called();control.cancel();worker.join(5)
        self.assertIsInstance(errors[0],SetupCancelled);callback.assert_not_called()
        completed=SetupControl();completed.commit(callback)
        self.assertFalse(completed.pause());completed.cancel();self.assertFalse(completed.cancelled)

    def test_user_pip_targets_cannot_escape_environment(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            with (root/'log').open('w') as log,patch.dict(os.environ,{'PIP_TARGET':'elsewhere','PIP_PREFIX':'elsewhere','PIP_USER':'yes','PYTHONPATH':'elsewhere'}):
                code="import os,json;open('env.json','w').write(json.dumps(dict(os.environ)))"
                SetupControl().run([sys.executable,'-c',code],log,root)
            env=json.loads((root/'env.json').read_text())
            for name in ('PIP_TARGET','PIP_PREFIX','PIP_USER','PYTHONPATH'):self.assertNotIn(name,env)
            self.assertEqual(env['PIP_CONFIG_FILE'],os.devnull)
