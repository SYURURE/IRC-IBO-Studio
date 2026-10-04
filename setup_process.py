"""Pause/resume only the installer subprocess tree; requires no third-party modules."""
import os
import signal
import subprocess
import threading
import time


class SetupCancelled(Exception):
    pass


class WindowsJob:
    """Own a job before its initially suspended process can spawn children.

    Uses documented Win32 job/Toolhelp/thread APIs. Never enumerates or suspends
    unrelated applications. Holding thread handles prevents thread-ID reuse.
    """
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.k = c.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            'SetInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            'QueryInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            'AssignProcessToJobObject': ([w.HANDLE, w.HANDLE], w.BOOL),
            'TerminateJobObject': ([w.HANDLE, w.UINT], w.BOOL),
            'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'CreateToolhelp32Snapshot': ([w.DWORD, w.DWORD], w.HANDLE),
            'Thread32First': ([w.HANDLE, c.c_void_p], w.BOOL),
            'Thread32Next': ([w.HANDLE, c.c_void_p], w.BOOL),
            'OpenThread': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'GetProcessIdOfThread': ([w.HANDLE], w.DWORD),
            'SuspendThread': ([w.HANDLE], w.DWORD),
            'ResumeThread': ([w.HANDLE], w.DWORD),
            'CloseHandle': ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.k, name); fn.argtypes = args; fn.restype = result

        class Basic(c.Structure):
            _fields_ = [('process_time', c.c_longlong), ('job_time', c.c_longlong),
                        ('flags', w.DWORD), ('min_ws', c.c_size_t), ('max_ws', c.c_size_t),
                        ('active', w.DWORD), ('affinity', c.c_size_t),
                        ('priority', w.DWORD), ('scheduling', w.DWORD)]

        class Extended(c.Structure):
            _fields_ = [('basic', Basic), ('io', c.c_ulonglong * 6),
                        ('memory', c.c_size_t * 4)]

        class ThreadEntry(c.Structure):
            _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('tid', w.DWORD),
                        ('pid', w.DWORD), ('priority', w.LONG), ('delta', w.LONG),
                        ('flags', w.DWORD)]

        self.ThreadEntry = ThreadEntry
        self.threads = {}
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        limits = Extended(); limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self.k.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            error = c.WinError(c.get_last_error()); self.k.CloseHandle(self.handle)
            self.handle = None; raise error

    def pids(self):
        c = self.c
        count = 32
        while count <= 65536:
            class IdList(c.Structure):
                _fields_ = [('assigned', c.c_uint32), ('count', c.c_uint32),
                            ('ids', c.c_size_t * count)]
            data = IdList()
            if self.k.QueryInformationJobObject(self.handle, 3, c.byref(data), c.sizeof(data), None):
                if data.assigned <= data.count:
                    return set(data.ids[:data.count])
            elif c.get_last_error() != 234:  # ERROR_MORE_DATA
                raise c.WinError(c.get_last_error())
            count = max(count * 2, data.assigned + 8)
        raise RuntimeError('セットアップの子プロセス数が上限を超えました。')

    def thread_ids(self, pids):
        c = self.c
        snapshot = self.k.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = self.ThreadEntry(); entry.size = c.sizeof(entry)
            ok = self.k.Thread32First(snapshot, c.byref(entry))
            while ok:
                if entry.pid in pids:
                    yield entry.tid
                entry.size = c.sizeof(entry)
                ok = self.k.Thread32Next(snapshot, c.byref(entry))
            if c.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise c.WinError(c.get_last_error())
        finally:
            self.k.CloseHandle(snapshot)

    def open_thread(self, tid, pids):
        # Query owner again after opening to protect against a recycled TID.
        handle = self.k.OpenThread(0x0002 | 0x0800, False, tid)
        if not handle:
            if self.c.get_last_error() == 87:  # thread already exited
                return None
            raise self.c.WinError(self.c.get_last_error())
        if self.k.GetProcessIdOfThread(handle) not in pids:
            self.k.CloseHandle(handle); return None
        return handle

    def attach_and_start(self, process):
        handle = self.k.OpenProcess(0x0100 | 0x0001, False, process.pid)
        if not handle:
            raise self.c.WinError(self.c.get_last_error())
        try:
            if not self.k.AssignProcessToJobObject(self.handle, handle):
                raise self.c.WinError(self.c.get_last_error())
        finally:
            self.k.CloseHandle(handle)
        tids = list(self.thread_ids({process.pid}))
        if len(tids) != 1:
            raise RuntimeError('インストーラーの初期スレッドを確認できませんでした。')
        thread = self.open_thread(tids[0], {process.pid})
        if not thread:
            raise RuntimeError('インストーラーが開始前に終了しました。')
        try:
            if self.k.ResumeThread(thread) == 0xffffffff:
                raise self.c.WinError(self.c.get_last_error())
        finally:
            self.k.CloseHandle(thread)

    def pause(self):
        try:
            # Repeat until all threads, including children created during the
            # snapshot, are suspended. No target mutex is acquired by this app.
            for _ in range(64):
                pids = self.pids()
                new = set(self.thread_ids(pids)) - self.threads.keys()
                if not new:
                    return
                for tid in new:
                    handle = self.open_thread(tid, pids)
                    if handle:
                        if self.k.SuspendThread(handle) == 0xffffffff:
                            error = self.c.WinError(self.c.get_last_error())
                            self.k.CloseHandle(handle); raise error
                        self.threads[tid] = handle
            raise RuntimeError('一時停止を完了できませんでした。')
        except Exception:
            self.resume()
            raise

    def resume(self):
        errors = []
        for tid, handle in list(self.threads.items()):
            if self.k.ResumeThread(handle) == 0xffffffff:
                errors.append(self.c.WinError(self.c.get_last_error()))
                continue
            self.k.CloseHandle(handle); del self.threads[tid]
        if errors:
            raise errors[0]

    def kill(self):
        if self.handle and not self.k.TerminateJobObject(self.handle, 1):
            raise self.c.WinError(self.c.get_last_error())

    def close(self):
        if self.handle:
            self.kill()
            deadline = time.monotonic() + 10
            while self.pids():
                if time.monotonic() > deadline:
                    raise RuntimeError('インストーラーの終了確認がタイムアウトしました。')
                time.sleep(0.02)
            for handle in self.threads.values():
                self.k.CloseHandle(handle)
            self.threads.clear()
            self.k.CloseHandle(self.handle); self.handle = None


class ProcessTree:
    def __init__(self, args, **kwargs):
        self.job = WindowsJob() if os.name == 'nt' else None
        self.process = None
        try:
            if self.job:
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW | 0x00000004
            else:
                kwargs['start_new_session'] = True
            self.process = subprocess.Popen(args, **kwargs)
            if self.job:
                self.job.attach_and_start(self.process)
        except Exception:
            if self.process is not None:
                self.process.kill(); self.process.wait()
            if self.job:
                self.job.close()
            raise

    def _signal(self, sig):
        try:
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            pass

    def pause(self):
        if self.job: self.job.pause()
        else: self._signal(signal.SIGSTOP)

    def resume(self):
        if self.job: self.job.resume()
        else: self._signal(signal.SIGCONT)

    def kill(self):
        if self.job: self.job.kill()
        else: self._signal(signal.SIGKILL)

    def close(self):
        self.kill()
        self.process.wait(timeout=10)
        if self.job: self.job.close()


class SetupControl:
    """Serialize spawn, pause and commit; cancellation never touches old installs."""
    def __init__(self):
        self.condition = threading.Condition(threading.RLock())
        self.paused = False
        self.cancelled = False
        self.finished = False
        self.tree = None

    def checkpoint(self):
        with self.condition:
            while self.paused and not self.cancelled:
                self.condition.wait()
            if self.cancelled:
                raise SetupCancelled()

    def pause(self):
        with self.condition:
            if self.finished or self.cancelled:
                return False
            if self.tree:
                self.tree.pause()
            self.paused = True
            return True

    def resume(self):
        with self.condition:
            if self.tree:
                self.tree.resume()
            self.paused = False
            self.condition.notify_all()

    def cancel(self):
        with self.condition:
            if self.finished:
                return
            if self.tree:
                self.tree.kill()
            self.cancelled = True
            self.paused = False
            self.condition.notify_all()

    def commit(self, callback):
        with self.condition:
            self.checkpoint()
            callback()
            self.finished = True

    def run(self, args, log, cwd):
        env = os.environ.copy()
        for name in ('PYTHONHOME', 'PYTHONPATH', 'PIP_TARGET', 'PIP_PREFIX', 'PIP_USER',
                     'PIP_ROOT', 'PIP_PYTHON'):
            env.pop(name, None)
        env.update(PYTHONIOENCODING='utf-8', PYTHONUTF8='1', PYTHONNOUSERSITE='1')
        # Ignore user pip target/prefix settings: all modules belong to the new venv.
        env['PIP_CONFIG_FILE'] = os.devnull
        with self.condition:
            self.checkpoint()
            log.write('\n> ' + repr([str(a) for a in args]) + '\n'); log.flush()
            tree = ProcessTree([str(a) for a in args], cwd=str(cwd), env=env,
                               stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            self.tree = tree
        try:
            while tree.process.poll() is None:
                with self.condition:
                    self.condition.wait(timeout=0.08)
            self.checkpoint()
            if tree.process.returncode:
                raise RuntimeError(f'処理が終了コード {tree.process.returncode} で停止しました。setup.logを確認してください。')
        finally:
            with self.condition:
                try:
                    tree.close()
                finally:
                    self.tree = None
