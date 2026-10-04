"""Double-click setup/launch using only Python's standard library.

No PowerShell, BAT, execution-policy change, elevation, or security exclusions.
"""
from pathlib import Path
import hashlib
import json
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import traceback
import uuid
from setup_process import SetupCancelled, SetupControl

BASE = Path(__file__).resolve().parent
ENV = BASE / '.venv'
STATE = BASE / '.studio-setup.json'
LOG = BASE / 'setup.log'


def venv_python(directory, windowed=False):
    if os.name == 'nt':
        return directory / 'Scripts' / ('pythonw.exe' if windowed else 'python.exe')
    return directory / 'bin' / 'python'


def environment_python(root=BASE, windowed=False):
    """Read the committed environment; also accept setups from v0.3.1–0.3.4."""
    try:
        state = json.loads((root / '.studio-setup.json').read_text(encoding='utf-8'))
        relative = Path(state.get('environment', '.venv'))
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('仮想環境の保存先が不正です。')
        directory = root / relative
    except FileNotFoundError:
        directory = root / '.venv'
    return venv_python(directory, windowed)


def required_modules(root=BASE):
    """The same list drives both the UI and installation; no stale duplicate list."""
    result = []
    for line in (root / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        spec = line.split('#', 1)[0].strip()
        if not spec:
            continue
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)(?:[<>=!~0-9.,* ]*)', spec)
        if not match:
            raise RuntimeError('requirements.txtに対応していない指定があります: ' + spec)
        result.append((match.group(1), spec))
    return result


def fingerprint(root=BASE):
    return hashlib.sha256((root / 'requirements.txt').read_bytes()).hexdigest()


def ready(root=BASE):
    try:
        state = json.loads((root / '.studio-setup.json').read_text(encoding='utf-8'))
        return (state['requirements'] == fingerprint(root)
                and state['directory'] == str(root.resolve())
                and environment_python(root).is_file()
                and environment_python(root, True).is_file())
    except (OSError, ValueError, KeyError, TypeError):
        return False


def host_python():
    exe = Path(getattr(sys, '_base_executable', None) or sys.executable)
    if exe.name.lower() == 'pythonw.exe':
        exe = exe.with_name('python.exe')
    return exe


def run_command(args, log, cwd=BASE):
    SetupControl().run(args, log, cwd)


def prepare(progress, root=BASE, runner=None, control=None, module_update=None):
    control = control or SetupControl()
    runner = runner or control.run
    module_update = module_update or (lambda name, state: None)
    if sys.version_info < (3, 10) or struct.calcsize('P') != 8:
        raise RuntimeError('64-bitのPython 3.10以上が必要です。Python公式版を導入してください。')
    for name in ('requirements.txt', 'run_app.py', 'irc_ibo_studio/app.py'):
        if not (root / name).is_file():
            raise RuntimeError('ZIPをフォルダーごと「すべて展開」してから実行してください。')
    modules = required_modules(root)
    lock = root / '.studio-setup.lock'
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError('別のセットアップが実行中です。終了を待ってください。\n'
                           '以前の処理が強制終了した場合は、実行中でないことを確認して\n'
                           '.studio-setup.lockを削除してから再実行してください。') from None
    os.close(fd)
    candidate = root / '.studio-environments' / ('setup-' + uuid.uuid4().hex)
    committed = False
    try:
        # Never move a venv (its installed entry points contain absolute paths).
        # Atomically switch only the marker after validation; keep any old setup.
        control.checkpoint()
        candidate.mkdir(parents=True)
        for name, _ in modules:
            module_update(name, '未インストール')
        with (root / 'setup.log').open('w', encoding='utf-8') as log:
            progress('1 / 3　Python仮想環境を構築しています…\nモジュールを導入するためのpipも、この仮想環境内に準備します。')
            runner([host_python(), '-m', 'venv', candidate], log, cwd=root)
            python = venv_python(candidate)
            for index, (name, spec) in enumerate(modules, 1):
                control.checkpoint()
                progress(f'2 / 3　Pythonモジュールをインストールしています…\n{index} / {len(modules)}：{name}')
                module_update(name, 'インストール中')
                runner([python, '-m', 'pip', 'install', '--disable-pip-version-check',
                        '--no-input', '--no-cache-dir', spec], log, cwd=root)
                control.checkpoint()
                module_update(name, 'インストール終了')
            control.checkpoint()
            progress('3 / 3　起動に必要な部品を確認しています…')
            runner([python, '-c', 'import tkinter, numpy, PIL, imageio_ffmpeg; '
                    'from irc_ibo_studio.app import App; '
                    'r=tkinter.Tk(); r.withdraw(); r.destroy()'], log, cwd=root)
        state = {'directory': str(root.resolve()), 'requirements': fingerprint(root),
                 'environment': candidate.relative_to(root).as_posix()}
        temporary = candidate / 'setup-state.json'
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        control.commit(lambda: os.replace(temporary, root / '.studio-setup.json'))
        committed = True
    finally:
        try:
            if not committed and candidate.exists():
                # Rollback is no longer cancellable; let it finish before closing.
                with control.condition:
                    while control.paused and not control.cancelled:
                        control.condition.wait()
                    control.finished = True
                progress('今回の仮想環境と、新しくインストールしたモジュールを削除しています…')
                try:
                    shutil.rmtree(candidate)
                except OSError as exc:
                    raise RuntimeError('中止しましたが、今回の仮想環境の削除が完了していません。\n'
                                       f'削除対象: {candidate}\n{exc}') from exc
                for name, _ in modules:
                    module_update(name, '未インストール')
        finally:
            lock.unlink(missing_ok=True)


def start_application(root=BASE):
    if not ready(root):
        raise RuntimeError('初回セットアップが必要です。\n01_setup.pywをダブルクリックしてください。\n'
                           'フォルダーを移動した場合も、セットアップを再実行してください。')
    with (root / 'launch.log').open('a', encoding='utf-8') as log:
        return subprocess.Popen([str(environment_python(root, True)), str(root / 'run_app.py')],
                                cwd=str(root), stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def set_progress_state(bar, state):
    """Running is indeterminate; success must stay visibly at 100%."""
    bar.stop()
    if state=='running':
        bar.configure(mode='indeterminate',maximum=100,value=0);bar.start()
    else:
        bar.configure(mode='determinate',maximum=100,value=100 if state=='done' else 0)


def main(mode):
    import tkinter as tk
    from tkinter import ttk, messagebox
    root = tk.Tk()
    root.title('IRC IBO Studio | 初回セットアップ' if mode == 'setup' else 'IRC IBO Studio')
    if mode == 'start':
        root.withdraw()
        try:
            process = start_application()
            def check():
                code = process.poll()
                if code is not None and code != 0:
                    messagebox.showerror('起動できませんでした',
                                         'launch.log / startup_error.txtを確認してください。', parent=root)
                root.destroy()
            root.after(1500, check); root.mainloop()
        except Exception as exc:
            messagebox.showerror('起動の準備', str(exc), parent=root); root.destroy()
        return
    root.geometry('780x590'); root.minsize(650,560)
    frame = ttk.Frame(root, padding=24); frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='初回セットアップ', font=('Yu Gothic UI',17,'bold')).pack(anchor='w')
    intro = ttk.Label(frame, text='① Python仮想環境を構築 → ② モジュールをインストール → ③ 起動確認\n'
                      'インターネットに接続してください。既存のPython環境は保持します。', wraplength=700)
    intro.pack(fill='x', pady=(14,18))
    status = tk.StringVar(value='準備を開始します…')
    label=ttk.Label(frame, textvariable=status, wraplength=550, justify='left')
    label.pack(fill='x', pady=8)
    bar=ttk.Progressbar(frame, mode='indeterminate');bar.pack(fill='x', pady=12)
    modules = required_modules()
    ttk.Label(frame, text='今回構築する仮想環境に導入するモジュール',
              font=('Yu Gothic UI',10,'bold')).pack(anchor='w',pady=(10,6))
    listing = ttk.Frame(frame);listing.pack(fill='both',expand=True)
    table = ttk.Treeview(listing, columns=('spec','purpose','state'), show='headings', height=3)
    for column, title, width in [('spec','モジュール・必要バージョン',235),
                                  ('purpose','用途',235),('state','状態',170)]:
        table.heading(column,text=title);table.column(column,width=width,minwidth=120,stretch=True)
    table.grid(row=0,column=0,sticky='nsew')
    vertical=ttk.Scrollbar(listing,orient='vertical',command=table.yview)
    vertical.grid(row=0,column=1,sticky='ns');table.configure(yscrollcommand=vertical.set)
    horizontal=ttk.Scrollbar(listing,orient='horizontal',command=table.xview)
    horizontal.grid(row=1,column=0,sticky='ew');table.configure(xscrollcommand=horizontal.set)
    listing.rowconfigure(0,weight=1);listing.columnconfigure(0,weight=1)
    for state, color in [('未インストール','#666666'),('インストール中','#986400'),
                         ('インストール終了','#167347')]:
        table.tag_configure(state,foreground=color)
    purposes={'numpy':'座標・数値計算','Pillow':'画像処理・PNG/GIF','imageio-ffmpeg':'動画作成用FFmpeg'}
    rows={name:table.insert('', 'end', values=(spec,purposes.get(name,'依存モジュール'),'未インストール'),
                            tags=('未インストール',)) for name,spec in modules}
    note=ttk.Label(frame,text='pipは工程①で準備します。追加の依存モジュールも専用の仮想環境内に導入します。\n'
                   'キャンセル → 一時停止して確認 ／ OK：中止・今回の環境を削除 ／ Cancel：再開',
                   justify='left',wraplength=700)
    note.pack(fill='x',pady=(12,16))
    frame.bind('<Configure>',lambda e:[w.configure(wraplength=max(200,e.width-48)) for w in (intro,label,note)])
    buttons=ttk.Frame(frame);buttons.pack(side='bottom',fill='x')
    events=queue.Queue(); busy=False; control=None; paused=False; latest='準備を開始します…'
    def close():
        nonlocal paused
        if not busy:
            root.destroy();return
        if control is None or control.cancelled or paused:
            return
        try:
            if not control.pause():
                return
        except Exception as exc:
            messagebox.showerror('一時停止できませんでした',str(exc),parent=root);return
        paused=True;bar.stop();status.set('一時停止中です。中止するか、確認ウィンドウで選択してください。')
        stop.configure(state='disabled')
        answer=messagebox.askokcancel('セットアップを中止しますか？',
            'インストール作業を一時停止しました。\n\n'
            'OK：インストールを中止し、今回新しく作った仮想環境と、\n'
            'その中にインストールしたモジュールをすべて削除します。\n'
            '既存のPythonや、以前に完成したセットアップは保持します。\n\n'
            'Cancel：一時停止した作業を再開します。',parent=root,default='cancel')
        try:
            if answer:
                control.cancel();status.set('中止処理中です。今回の仮想環境を削除するまでお待ちください…')
            else:
                control.resume();status.set(latest);bar.start();stop.configure(state='normal')
            paused=False
        except Exception as exc:
            # Remain paused, and allow the same confirmation to be retried.
            paused=False
            stop.configure(state='normal')
            messagebox.showerror('処理を完了できませんでした',str(exc),parent=root)
    def open_app():
        try:start_application();root.destroy()
        except Exception as exc:messagebox.showerror('起動できませんでした',str(exc),parent=root)
    launch=ttk.Button(buttons,text='Studioを起動',command=open_app,state='disabled');launch.pack(side='right')
    retry=ttk.Button(buttons,text='再試行',state='disabled');retry.pack(side='right',padx=8)
    stop=ttk.Button(buttons,text='キャンセル',command=close);stop.pack(side='left')
    def begin():
        nonlocal busy,control,paused
        busy=True;paused=False;control=SetupControl()
        set_progress_state(bar,'running');retry.configure(state='disabled');launch.configure(state='disabled')
        stop.configure(text='キャンセル',state='normal')
        def worker():
            try:
                prepare(lambda text:events.put(('progress',text)),control=control,
                        module_update=lambda name,state:events.put(('module',(name,state))))
                events.put(('done',''))
            except SetupCancelled:events.put(('cancelled','中止しました。今回の仮想環境と導入したモジュールは削除済みです。\n再試行すると、仮想環境の構築からやり直します。'))
            except Exception as exc:events.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()
    retry.configure(command=begin)
    def poll():
        nonlocal busy,latest
        try:
            while True:
                event,text=events.get_nowait()
                if event=='progress':
                    latest=text
                    if not paused:status.set(text)
                elif event=='module':
                    name,state=text;table.set(rows[name],'state',state);table.item(rows[name],tags=(state,))
                else:
                    busy=False;set_progress_state(bar,event)
                    stop.configure(text='閉じる',state='normal')
                    if event=='done':
                        status.set('準備が完了しました！\n次回からは02_start.pywをダブルクリックしてください。')
                        launch.configure(state='normal')
                    else:
                        status.set(text);retry.configure(state='normal')
        except queue.Empty:pass
        root.after(100,poll)
    root.protocol('WM_DELETE_WINDOW',close)
    root.after(100,poll);root.after(200,begin);root.mainloop()


def entry(mode):
    try:main(mode)
    except Exception:
        details=traceback.format_exc()
        try:(BASE/'launcher_error.txt').write_text(details,encoding='utf-8')
        except OSError:pass
        # Native fallback also works if Tcl/Tk is missing from installed Python.
        if os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                '起動の準備に失敗しました。Tcl/Tkを含むPython公式版を確認してください。\n\n'+details,
                'IRC IBO Studio',0x10)
        else:raise
