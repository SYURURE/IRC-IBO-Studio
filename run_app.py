import sys
try:
    from irc_ibo_studio.app import main
    main()
except Exception:
    import traceback
    error=traceback.format_exc()
    from pathlib import Path
    Path(__file__).with_name('startup_error.txt').write_text(error,encoding='utf-8')
    print(error)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root=tk.Tk();root.withdraw()
        messagebox.showerror('起動できませんでした', '01_setup.pywを再実行してください。\n詳細はstartup_error.txtに保存しました。\n\n'+error[-1800:],parent=root)
        root.destroy()
    except Exception:
        pass
    sys.exit(1)
