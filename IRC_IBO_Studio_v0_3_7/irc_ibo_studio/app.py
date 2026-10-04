"""IRC IBO Studio: local desktop workbench. No calculations run on import."""
from __future__ import annotations
import json, math, os, queue, subprocess, sys, threading, webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont
import numpy as np
from PIL import Image, ImageTk, ImageDraw
from .core import load_trajectory, save_xyz, align_frames, save_project, load_project
from .merge import merge_irc_logs, selection_indices
from .media import make_iboview_script, export_movie, list_frames
from .viewer import SceneRenderer, hit_test, trajectory_extent, load_font
from .ibo_guide import INTRO as IBO_GUIDE_INTRO, STEPS as IBO_GUIDE_STEPS, SOURCES as IBO_GUIDE_SOURCES

BASE = Path(__file__).resolve().parent.parent
CONFIG = Path(os.environ.get('APPDATA', str(Path.home()/'.config'))) / 'IRC_IBO_Studio' / 'settings.json'
TEAL='#087f8c'; INK='#183342'; MUTED='#526d7b'; BG='#f3f6f8'

class ScrollFrame(ttk.Frame):
    """A vertically scrollable form; scrolling never steals the 3D viewer's wheel."""
    def __init__(self,parent,**kwargs):
        super().__init__(parent,**kwargs)
        self.canvas=tk.Canvas(self,bg=BG,highlightthickness=0,width=kwargs.get('width',1),height=1)
        self.scrollbar=ttk.Scrollbar(self,orient='vertical',command=self.canvas.yview)
        self.scrollbar.pack(side='right',fill='y')
        self.canvas.pack(side='left',fill='both',expand=True)
        self.content=ttk.Frame(self.canvas,padding=(0,0,12,4))
        self.window=self.canvas.create_window((0,0),window=self.content,anchor='nw')
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.bind('<Configure>',self._resize)
        self.content.bind('<Configure>',self._region)
        # The shared application handler dispatches only to the form under the
        # pointer. Do not bind/unbind all wheel handlers when forms are destroyed.
        root=self.winfo_toplevel()
        if not hasattr(root,'_scroll_forms'):
            root._scroll_forms=[]
            for event in ('<MouseWheel>','<Button-4>','<Button-5>'):
                root.bind(event,self._dispatch_wheel,add='+')
        root._scroll_forms.append(self)

    def _resize(self,event):
        self.canvas.itemconfigure(self.window,width=event.width)

    def _region(self,event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        if self.content.winfo_reqheight()<=self.canvas.winfo_height():
            self.canvas.yview_moveto(0)

    @staticmethod
    def _dispatch_wheel(event):
        root=event.widget.winfo_toplevel()
        widget=root.winfo_containing(event.x_root,event.y_root)
        forms=getattr(root,'_scroll_forms',())
        while widget is not None:
            for form in forms:
                if widget is form.content or widget is form.canvas:
                    if form.content.winfo_reqheight()>form.canvas.winfo_height():
                        delta=getattr(event,'delta',0)
                        direction=-1 if getattr(event,'num',None)==4 or delta>0 else 1
                        form.canvas.yview_scroll(direction*3,'units')
                        return 'break'
                    return None
            widget=getattr(widget,'master',None)

def action_layout(width,sizes,gap=7,row_gap=3):
    """Return pixel positions for wrapping controls of differing widths."""
    positions=[];x=y=row_height=0
    for button_width,button_height in sizes:
        if x and x+button_width>width:
            x=0;y+=row_height+row_gap;row_height=0
        positions.append((x,y))
        x+=button_width+gap;row_height=max(row_height,button_height)
    return positions,y+row_height


class ActionRow(ttk.Frame):
    """Wrap a row of buttons instead of hiding labels on narrow/high-DPI windows."""
    def __init__(self,parent,**kwargs):
        super().__init__(parent,**kwargs)
        self.bind('<Configure>',self._layout)

    def add_button(self,**kwargs):
        button=ttk.Button(self,**kwargs)
        button.place(x=0,y=0)
        return button

    def _layout(self,event):
        buttons=self.winfo_children()
        positions,height=action_layout(event.width,[(b.winfo_reqwidth(),b.winfo_reqheight()) for b in buttons])
        for button,(x,y) in zip(buttons,positions):button.place_configure(x=x,y=y)
        if int(self.cget('height'))!=height:self.configure(height=height)


def column_layout(width, left_height, right_height):
    """Explicit widths keep text requests from feeding back into column sizing."""
    width=max(1,int(width));gap=12
    if width>=940:
        first=(width-gap)//2
        return (0,0,first),(first+gap,0,width-first-gap),max(left_height,right_height)
    return (0,0,width),(0,left_height+gap,width),left_height+gap+right_height


class StableColumns(ttk.Frame):
    """Two responsive columns without grid's requested-width feedback loop.

    The outer scroll canvas owns width. Children use explicit place widths;
    only their natural heights contribute to this frame's requested height.
    """
    def __init__(self,parent):
        super().__init__(parent,height=1)
        self.left=ttk.Frame(self);self.right=ttk.Frame(self)
        self.pending=None;self.last_width=None;self.last_layout=None
        self.bind('<Configure>',self._resize)
        for child in (self.left,self.right):
            child.bind('<Configure>',self._schedule)
        self._schedule()

    def _resize(self,event):
        if event.width!=self.last_width:
            self.last_width=event.width;self._schedule()

    def _schedule(self,event=None):
        if self.pending is None:self.pending=self.after_idle(self._layout)

    def _layout(self):
        self.pending=None
        placement=column_layout(self.winfo_width(),self.left.winfo_reqheight(),self.right.winfo_reqheight())
        if placement==self.last_layout:return
        self.last_layout=placement
        for child,(x,y,width) in zip((self.left,self.right),placement[:2]):
            child.place(x=x,y=y,width=width)
        if int(self.cget('height'))!=placement[2]:self.configure(height=placement[2])

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('IRC IBO Studio 0.3.7')
        width=max(960,min(1220,self.winfo_screenwidth()-80))
        height=max(640,min(780,self.winfo_screenheight()-110))
        self.geometry(f'{width}x{height}'); self.minsize(960,640)
        self.configure(bg=BG)
        self.trajectory=None; self.display_frames=[]; self.index=0; self.playing=False; self.play_job=None
        self.busy=False; self.events=queue.Queue(); self.cancel_event=threading.Event()
        self.preview_images=[]; self.preview_dir=''; self.png_index=0; self.png_playing=False; self.png_job=None; self.preview_photo=None
        self.yaw=-.45; self.pitch=.65; self.zoom=1.0; self.pan=(0.,0.); self.drag=None
        self.picked=[];self.scene_renderer=SceneRenderer();self.scene_projection=None
        self.scene_image=None;self.scene_photo=None;self.scene_extent=None;self.scene_job=None
        self.settings={}
        try:self.settings=json.loads(CONFIG.read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        style=ttk.Style(self); style.theme_use('clam')
        style.configure('.',font=('Yu Gothic UI',10),background=BG,foreground=INK)
        style.configure('TFrame',background=BG)
        style.configure('TLabel',background=BG)
        style.configure('TButton',padding=(10,6),background='#e6eef1',bordercolor='#b9cbd2')
        style.map('TButton',background=[('active','#d7e7eb')],foreground=[('disabled','#758790')])
        style.configure('Accent.TButton',background=TEAL,foreground='white',padding=(12,7))
        style.map('Accent.TButton',background=[('disabled','#c7d4d9'),('active','#086775')],foreground=[('disabled','#607580'),('!disabled','white')])
        style.configure('TNotebook',borderwidth=0)
        style.configure('TNotebook.Tab',padding=(15,8))
        style.map('TNotebook.Tab',background=[('selected','white'),('!selected','#e2eaee')],foreground=[('selected',TEAL)])
        for widget_style in ('TEntry','TCombobox','TSpinbox'):
            style.configure(widget_style,fieldbackground='white',foreground=INK,insertcolor=INK,padding=5)
            style.map(widget_style,fieldbackground=[('disabled','#e7edef'),('readonly','white')],foreground=[('disabled','#758790'),('readonly',INK)])
        style.configure('TCheckbutton',padding=(0,3))
        style.configure('TRadiobutton',padding=(0,4))
        style.configure('TLabelframe',bordercolor='#cbd9df',borderwidth=1)
        style.configure('TLabelframe.Label',foreground=TEAL,font=('Yu Gothic UI',10,'bold'))
        self.ui_font=tkfont.Font(self,font=('Yu Gothic UI',10))
        style.configure('Treeview',rowheight=max(28,self.ui_font.metrics('linespace')+10),fieldbackground='white',background='white')
        style.configure('Treeview.Heading',background='#e2ebef',padding=(6,6))
        style.map('Treeview',background=[('selected',TEAL)],foreground=[('selected','white')])
        self.option_add('*TCombobox*Listbox.background','white')
        self.option_add('*TCombobox*Listbox.foreground',INK)
        self.option_add('*TCombobox*Listbox.selectBackground',TEAL)
        self.option_add('*TCombobox*Listbox.selectForeground','white')
        self.protocol('WM_DELETE_WINDOW',self.close)
        self._build()
        self.png_dir.trace_add('write',self.png_directory_changed)
        self.after(100,self._poll)

    def _build(self):
        head=ttk.Frame(self,padding=(16,7)); head.pack(fill='x')
        ttk.Label(head,text='IRC IBO Studio',font=('Segoe UI',16,'bold')).pack(side='left')
        ttk.Label(head,text='0.3.7',foreground=MUTED).pack(side='left',padx=10)
        ttk.Button(head,text='使い方',command=lambda:webbrowser.open((BASE/'GUIDE.html').as_uri())).pack(side='right')
        self.tabs=ttk.Notebook(self); self.tabs.pack(fill='both',expand=True,padx=12)
        self.path_tab=ttk.Frame(self.tabs,padding=10); self.ibo_tab=ttk.Frame(self.tabs,padding=12)
        self.movie_tab=ttk.Frame(self.tabs,padding=10)
        for tab,name in [(self.path_tab,'1  経路'),(self.ibo_tab,'2  IboView'),(self.movie_tab,'3  動画')]:self.tabs.add(tab,text=name)
        self._path_ui(); self._ibo_ui(); self._movie_ui()
        foot=ttk.Frame(self,padding=(16,6)); foot.pack(side='bottom',fill='x',before=self.tabs)
        self.status=tk.StringVar(value='ログまたはXYZを開くか、サンプルを読み込んでください。')
        self.cancel_button=ttk.Button(foot,text='処理を中止',command=self.cancel_event.set,state='disabled');self.cancel_button.pack(side='right')
        self.status_preview=tk.StringVar(value=self.status.get())
        self.status.trace_add('write',lambda *_:self.status_preview.set(self._short(self.status.get().replace('\n',' '),180)))
        status=ttk.Label(foot,textvariable=self.status_preview,foreground=MUTED,wraplength=850)
        status.pack(side='left',fill='x',expand=True,padx=(0,10))
        foot.bind('<Configure>',lambda e:status.configure(wraplength=max(200,e.width-self.cancel_button.winfo_width()-52)))

    def _path_ui(self):
        bar=ActionRow(self.path_tab);bar.pack(fill='x',pady=(0,10))
        for label,cmd in [('IRC / XYZを開く',self.open_path),('人工サンプルを開く',lambda:self.read_path(BASE/'examples/synthetic_water_bend.xyz')),('プロジェクトを開く',self.open_project),('プロジェクトを保存',self.save_project)]:
            bar.add_button(text=label,command=cmd,style='Accent.TButton' if label=='IRC / XYZを開く' else 'TButton')
        self.merge_button=bar.add_button(text='Forward ＋ Reverseを統合',command=self.open_merge_dialog)
        self.path_panes=body=ttk.Panedwindow(self.path_tab,orient='horizontal');body.pack(fill='both',expand=True)
        left=ttk.Frame(body,width=310,padding=(0,0,4,0));right=ttk.Frame(body);body.add(left,weight=0);body.add(right,weight=1)
        self.info=tk.StringVar(value='構造データがありません')
        info=ttk.Label(left,textvariable=self.info,wraplength=300,justify='left');info.pack(fill='x',pady=(0,5))
        left.bind('<Configure>',lambda e:info.configure(wraplength=max(180,e.width-8)))
        ttk.Button(left,text='読み込み情報・注意点',command=self.show_trajectory_info).pack(fill='x',pady=(0,6))
        treebox=ttk.Frame(left);treebox.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(treebox,columns=('point','energy'),show='headings',height=6,selectmode='browse')
        self.tree.heading('point',text='方向 / 元の点');self.tree.heading('energy',text='E / Hartree')
        self.tree.column('point',width=max(100,self.ui_font.measure('方向 / 元の点')+16),stretch=False)
        self.tree.column('energy',width=max(160,self.ui_font.measure('-1130.123456789')+16),minwidth=130)
        treebox.rowconfigure(0,weight=1);treebox.columnconfigure(0,weight=1)
        self.tree.grid(row=0,column=0,sticky='nsew')
        scroll=ttk.Scrollbar(treebox,command=self.tree.yview);scroll.grid(row=0,column=1,sticky='ns');self.tree.configure(yscrollcommand=scroll.set)
        horizontal=ttk.Scrollbar(treebox,orient='horizontal',command=self.tree.xview);horizontal.grid(row=1,column=0,sticky='ew');self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.bind('<<TreeviewSelect>>',self.tree_select)
        self.tree.tag_configure('ts',background='#fff0d9',foreground='#803c12')
        sample=ttk.LabelFrame(left,text='書き出す点を選択',padding=8);sample.pack(side='bottom',fill='x',pady=(8,0),before=treebox)
        self.start=tk.StringVar(value='0');self.end=tk.StringVar(value='0');self.stride=tk.StringVar(value='10')
        rr=ttk.Frame(sample);rr.pack(fill='x')
        for column,(label,var) in enumerate([('開始',self.start),('終了',self.end),('間隔',self.stride)]):
            rr.columnconfigure(column,weight=1)
            ttk.Label(rr,text=label).grid(row=0,column=column,sticky='w')
            ttk.Entry(rr,textvariable=var,width=5).grid(row=1,column=column,sticky='ew',padx=(0,6))
        self._wrapped(sample,'位置番号は0開始。両端と統合TSを含めます。',font=('Yu Gothic UI',9),foreground=MUTED).pack(fill='x',pady=4)
        self.align=tk.BooleanVar(value=False)
        ttk.Checkbutton(sample,text='重原子で向きをそろえる',variable=self.align,command=self.refresh_alignment).pack(anchor='w')
        self.xyz_button=ttk.Button(sample,text='選択範囲をXYZへ保存',command=self.export_xyz);self.xyz_button.pack(fill='x',pady=(7,0))
        display_row=ActionRow(right);display_row.pack(fill='x',padx=(10,0),pady=(0,5))
        self.labels=tk.BooleanVar(value=False);self.show_bonds=tk.BooleanVar(value=True)
        ttk.Checkbutton(display_row,text='原子番号・元素',variable=self.labels,command=self.draw_scene).place(x=0,y=0)
        ttk.Checkbutton(display_row,text='結合',variable=self.show_bonds,command=self.draw_scene).place(x=0,y=0)
        display_row.add_button(text='視点を戻す',command=self.reset_view)
        self.scene_save_button=display_row.add_button(text='PNG保存',command=self.save_scene_png)
        self.scene=tk.Canvas(right,bg='white',highlightthickness=1,highlightbackground='#cbd9df',width=400,height=220);self.scene.pack(fill='both',expand=True,padx=(10,0))
        self.scene.bind('<Configure>',lambda e:self.request_scene_draw())
        self.scene.bind('<ButtonPress-1>',self.begin_scene_drag)
        self.scene.bind('<B1-Motion>',self.rotate)
        self.scene.bind('<ButtonRelease-1>',self.end_scene_drag)
        self.scene.bind('<Escape>',lambda e:self.clear_picks())
        self.scene.bind('<MouseWheel>',self.wheel)
        self.scene.bind('<Button-4>',lambda e:self.scale_view(1.12));self.scene.bind('<Button-5>',lambda e:self.scale_view(1/1.12))
        controls=ttk.Frame(right);controls.pack(side='bottom',fill='x',before=self.scene)
        measure=ttk.Frame(controls);measure.pack(fill='x',padx=(10,0),pady=(5,0))
        self.measurement=tk.StringVar(value='距離：原子を2つクリック')
        self.clear_measurement_button=ttk.Button(measure,text='距離選択を解除',command=self.clear_picks,state='disabled')
        self.clear_measurement_button.pack(side='right',padx=(6,0))
        measurement_label=ttk.Label(measure,textvariable=self.measurement,foreground=MUTED,wraplength=400)
        measurement_label.pack(side='left',fill='x',expand=True)
        measure.bind('<Configure>',lambda e:measurement_label.configure(wraplength=max(120,e.width-self.clear_measurement_button.winfo_reqwidth()-10)))
        nav=ttk.Frame(controls);nav.pack(fill='x',padx=(10,0),pady=(5,0))
        self.play_btn=ttk.Button(nav,text='▶ 再生',command=self.toggle_play);self.play_btn.pack(side='left')
        self.frame_label=tk.StringVar(value='—');frame_info=ttk.Label(nav,textvariable=self.frame_label,foreground=MUTED,wraplength=450);frame_info.pack(side='right',padx=(8,0))
        nav.bind('<Configure>',lambda e:frame_info.configure(wraplength=max(120,e.width-self.play_btn.winfo_reqwidth()-12)))
        self.slider=ttk.Scale(controls,from_=0,to=1,command=self.slider_move);self.slider.pack(fill='x',padx=(12,0),pady=4)
        self.plot=tk.Canvas(controls,height=90,bg='white',highlightthickness=1,highlightbackground='#d4e0e5');self.plot.pack(fill='x',padx=(10,0))
        self.plot.bind('<Configure>',lambda e:self.draw_plot());self.plot.bind('<Button-1>',self.plot_click)
        hint=ttk.Label(controls,text='ドラッグ：回転 / Shift＋ドラッグ：移動 / ホイール：拡大縮小\n表示専用（原子座標は変更しません）。結合は距離から推定。再生は実時間ではありません。',font=('Yu Gothic UI',9),foreground=MUTED,wraplength=650)
        hint.pack(padx=12,pady=(6,0),anchor='w')
        controls.bind('<Configure>',lambda e:hint.configure(wraplength=max(300,e.width-24)))
        self._limit_panes(body,left_min=max(290,min(400,self.tree.winfo_reqwidth()+20)),right_min=350)

    def _wrapped(self,parent,text='',**kwargs):
        label=ttk.Label(parent,text=text,wraplength=600,justify='left',**kwargs)
        def resize(event):
            width=max(120,event.width-28)
            if int(label.cget('wraplength'))!=width:label.configure(wraplength=width)
        parent.bind('<Configure>',resize,add='+')
        return label

    @staticmethod
    def _short(text,length=65):
        return text if len(text)<=length else text[:length-1]+'…'

    @staticmethod
    def _limit_panes(panes,left_min=290,right_min=350):
        def constrain(event=None):
            width=panes.winfo_width()
            if width<left_min+right_min:return
            current=panes.sashpos(0)
            target=max(left_min,min(current,width-right_min))
            if target!=current:panes.sashpos(0,target)
        panes.bind('<Configure>',constrain,add='+')
        panes.bind('<ButtonRelease-1>',constrain,add='+')

    def _field(self,parent,label,var,width=40,browse=None):
        box=ttk.Frame(parent);box.pack(fill='x',pady=6)
        ttk.Label(box,text=label).pack(anchor='w',pady=(0,3))
        row=ttk.Frame(box);row.pack(fill='x')
        if browse:ttk.Button(row,text='選択',command=browse).pack(side='right',padx=(8,0))
        ttk.Entry(row,textvariable=var,width=width).pack(side='left',fill='x',expand=True)

    def _ibo_ui(self):
        content=self.ibo_tab
        self._wrapped(content,'選んだ経路をIboViewへ',font=('Yu Gothic UI',14,'bold')).pack(fill='x',pady=(0,4))
        self._wrapped(content,'構造を渡す → IboViewでIBOを計算・表示 → PNG連番を保存',foreground=MUTED).pack(fill='x',pady=(0,12))
        nav=ActionRow(content);nav.pack(side='bottom',fill='x',pady=(10,0))
        self.ibo_steps=ttk.Notebook(content);self.ibo_steps.pack(fill='both',expand=True)
        self.ibo_step_pages=[];self.ibo_step_scrolls=[]
        for title in ('① 構造を渡す','② IBOを計算・表示','③ PNGを保存'):
            page=ttk.Frame(self.ibo_steps,padding=10);self.ibo_steps.add(page,text=title)
            scroll=ScrollFrame(page);scroll.pack(fill='both',expand=True)
            self.ibo_step_pages.append(page);self.ibo_step_scrolls.append(scroll)
        first,middle,last=[scroll.content for scroll in self.ibo_step_scrolls]
        def move_step(direction):
            index=self.ibo_steps.index(self.ibo_steps.select())+direction
            if index==3:self.tabs.select(self.movie_tab)
            elif 0<=index<3:self.ibo_steps.select(index)
        previous=nav.add_button(text='← 前の工程',command=lambda:move_step(-1))
        following=nav.add_button(text='次へ：② IBOを計算・表示 →',command=lambda:move_step(1),style='Accent.TButton')
        def update_step(event=None):
            index=self.ibo_steps.index(self.ibo_steps.select())
            previous.configure(state='disabled' if index==0 else 'normal')
            following.configure(text=('次へ：② IBOを計算・表示 →','次へ：③ PNGを保存 →','「3 動画」へ →')[index])
        self.ibo_steps.bind('<<NotebookTabChanged>>',update_step);update_step()
        setup=ttk.LabelFrame(first,text='IboViewへ渡す構造をコピー',padding=12);setup.pack(fill='x')
        self.ibo_exe=tk.StringVar(value=self.settings.get('iboview',''))
        self._field(setup,'IboView実行ファイル（iboview.exe）',self.ibo_exe,width=24,browse=lambda:self.pick_exe(self.ibo_exe))
        launch=ActionRow(setup);launch.pack(fill='x',pady=(5,2))
        launch.add_button(text='構造をコピー ＋ 起動',style='Accent.TButton',command=self.launch_ibo)
        launch.add_button(text='構造のコピーのみ',command=self.copy_xyz)
        self.ibo_metadata=tk.StringVar(value='まず「1 経路」でIRC / XYZを読み込んでください。')
        self._wrapped(setup,textvariable=self.ibo_metadata,foreground=MUTED).pack(fill='x',pady=(8,0))
        self._wrapped(setup,'XYZに電荷・スピンの設定は渡りません。IboView側で確認してください。',foreground=MUTED).pack(fill='x',pady=(5,0))
        paste=ttk.LabelFrame(first,text='コピー後のIboView操作',padding=12);paste.pack(fill='x',pady=(12,0))
        self._wrapped(paste,IBO_GUIDE_STEPS[0][1]).pack(fill='x')
        box=ttk.LabelFrame(last,text='PNG連番の保存先とスクリプト',padding=12);box.pack(fill='x')
        self.png_dir=tk.StringVar(value='')
        self._field(box,'空のPNG保存フォルダー',self.png_dir,width=24,browse=lambda:self.pick_dir(self.png_dir))
        ttk.Button(box,text='PNG保存スクリプトをコピー',command=self.copy_script).pack(fill='x',pady=(6,8))
        self._wrapped(box,'IBOを表示したIboViewで Ctrl＋Shift＋V。保存終了後は「3 動画」へ。',foreground=MUTED).pack(fill='x')
        export_help=ttk.LabelFrame(last,text='IboViewで保存して動画へ進む',padding=12);export_help.pack(fill='x',pady=(12,0))
        self._wrapped(export_help,IBO_GUIDE_STEPS[6][1]).pack(fill='x')
        procedure=ttk.LabelFrame(middle,text='IboViewでの操作ガイド',padding=12);procedure.pack(fill='x')
        self._wrapped(procedure,IBO_GUIDE_INTRO,foreground=MUTED).pack(fill='x',pady=(0,12))
        for index,(heading,text) in enumerate(IBO_GUIDE_STEPS[1:6]):
            if index:ttk.Separator(procedure,orient='horizontal').pack(fill='x',pady=(2,12))
            self._wrapped(procedure,heading,foreground=TEAL,font=('Yu Gothic UI',10,'bold')).pack(fill='x',pady=(0,6))
            self._wrapped(procedure,text).pack(fill='x',pady=(0,12))
        self._wrapped(procedure,'表示名はIboViewのバージョンによって多少異なります。',foreground=MUTED).pack(fill='x',pady=(0,8))
        links=ActionRow(procedure);links.pack(fill='x')
        for title,url in IBO_GUIDE_SOURCES:
            links.add_button(text=title,command=lambda link=url:webbrowser.open(link))
        notes=ttk.LabelFrame(middle,text='計算条件のメモ',padding=12);notes.pack(fill='x',pady=(12,0))
        self.analysis_level=tk.StringVar(value='')
        self._field(notes,'IboViewで使用した汎関数・基底など',self.analysis_level,width=24)
        self._wrapped(notes,'この欄は記録専用です。計算設定はIboViewで行います。元のIRCと異なる条件を使う場合は、その違いを記録してください。',foreground=MUTED).pack(fill='x')

    def _movie_ui(self):
        title=ttk.Frame(self.movie_tab);title.pack(fill='x')
        ttk.Label(title,text='PNG連番から動画を作る',font=('Yu Gothic UI',14,'bold')).pack(side='left')
        ttk.Button(title,text='PNG連番を読み込む',command=self.load_pngs).pack(side='right')
        self._field(self.movie_tab,'PNG連番フォルダー（2 IboViewと共通）',self.png_dir,browse=lambda:self.pick_dir(self.png_dir))
        self.movie_panes=body=ttk.Panedwindow(self.movie_tab,orient='horizontal');body.pack(fill='both',expand=True,pady=(10,0))
        self.movie_scroll=ScrollFrame(body,width=310);body.add(self.movie_scroll,weight=0)
        left=self.movie_scroll.content
        ttk.Button(left,text='このフォルダーを読み込む',command=lambda:self.guard(lambda:self.read_pngs(self.png_dir.get()))).pack(fill='x',pady=(0,10))
        self.png_info=tk.StringVar(value='IboViewで保存したPNG連番を選んでください。')
        self._wrapped(left,textvariable=self.png_info).pack(fill='x',pady=(0,10))
        self.fps=tk.StringVar(value='12');self.kind=tk.StringVar(value='mp4');self.pingpong=tk.BooleanVar(value=False)
        options=ttk.LabelFrame(left,text='出力設定',padding=10);options.pack(fill='x')
        r=ttk.Frame(options);r.pack(fill='x')
        r.columnconfigure(0,weight=1);r.columnconfigure(1,weight=1)
        ttk.Label(r,text='FPS').grid(row=0,column=0,sticky='w');ttk.Spinbox(r,from_=1,to=60,textvariable=self.fps,width=5).grid(row=1,column=0,sticky='ew',padx=(0,8),pady=4)
        ttk.Label(r,text='形式').grid(row=0,column=1,sticky='w');ttk.Combobox(r,textvariable=self.kind,values=('mp4','gif'),state='readonly',width=5).grid(row=1,column=1,sticky='ew',pady=4)
        ttk.Checkbutton(options,text='往復再生にする',variable=self.pingpong).pack(anchor='w',pady=5)
        ttk.Button(options,text='動画を書き出す',style='Accent.TButton',command=self.make_movie).pack(fill='x',pady=(4,2))
        self.ffmpeg=tk.StringVar(value=self.settings.get('ffmpeg',''))
        advanced=ttk.LabelFrame(left,text='詳細設定',padding=10);advanced.pack(fill='x',pady=(12,0))
        self._field(advanced,'FFmpeg（通常は空欄）',self.ffmpeg,width=18,browse=lambda:self.pick_exe(self.ffmpeg))
        self._wrapped(left,'再生速度は表示用です。IRCは反応の実時間を示しません。\n\nPNGは同じ画像サイズ・視点で保存してください。',foreground=MUTED).pack(fill='x',pady=10)
        right=ttk.Frame(body,padding=(10,0,0,0));body.add(right,weight=1)
        self.image_canvas=tk.Canvas(right,bg='white',highlightthickness=1,highlightbackground='#cbd9df',width=400,height=220);self.image_canvas.pack(fill='both',expand=True)
        self.image_canvas.bind('<Configure>',lambda e:self.draw_png())
        ttk.Button(right,text='▶ / ❚❚ プレビュー',command=self.toggle_png).pack(anchor='w',pady=(8,0))
        self.png_slider=ttk.Scale(right,from_=0,to=1,command=self.png_slide);self.png_slider.pack(fill='x',pady=10)
        self._limit_panes(body,left_min=290,right_min=350)

    def guard(self,fn):
        try:return fn()
        except Exception as e:
            self.status.set(str(e));messagebox.showerror('処理できませんでした',str(e),parent=self)
            return None

    def read_path(self,path):
        if self.busy:return
        def go():
            t=load_trajectory(path);self.install_trajectory(t)
        self.guard(go)

    def open_path(self):
        p=filedialog.askopenfilename(filetypes=[('Gaussian IRC / XYZ','*.log *.LOG *.out *.OUT *.xyz *.XYZ'),('すべて','*')])
        if p:self.read_path(p)

    def open_merge_dialog(self):
        if self.busy:return
        self.merge_dialog=MergeDialog(self)

    def install_trajectory(self,t):
        if not t.frames:raise ValueError('読み込めるIRC点がありません。')
        self.stop_path_playback();self.trajectory=t;self.index=0
        self.picked=[];self.drag=None;self.scene_projection=None
        self.scene_renderer.clear_cache()
        if t.merge_info:self.align.set(True)
        self.start.set('0');self.end.set(str(len(t.frames)-1))
        self.ibo_metadata.set(f'{len(t.frames)}構造を読み込み済み / 元データ：電荷 {t.charge}・多重度 {t.multiplicity}')
        self.refresh_alignment()
        self.tree.delete(*self.tree.get_children())
        for i,f in enumerate(t.frames):
            direction={'forward':'F','reverse':'R','ts':'TS'}.get(f.branch,str(f.path))
            self.tree.insert('', 'end',iid=str(i),values=(f'{direction} / {f.point}',f'{f.energy:.9f}' if f.energy is not None else '—'),tags=('ts',) if f.branch=='ts' else ())
        self.slider.configure(to=max(1,len(t.frames)-1));self.set_frame(0)
        warning=f' · 注意 {len(t.warnings)}件' if t.warnings else ''
        self.info.set(f'{self._short(Path(t.source).name,34)}\n{len(t.frames)}構造 · {len(t.frames[0].symbols)}原子{warning}')
        self.status.set(f'{len(t.frames)}構造を読み込みました。元の原子順序を保持しています。')
        self.tabs.select(self.path_tab)

    def show_trajectory_info(self):
        if not self.trajectory:return
        t=self.trajectory
        f=t.frames[self.index]
        detail=f'表示中の構造：{f.source_file or t.source}\n元の行：{f.source_line} / 経路：{f.path} / 点：{f.point}\n'
        content=f'{t.source}\n\n計算条件：{t.method or "ログに記載なし"}\n電荷 {t.charge} / 多重度 {t.multiplicity}\n\n'+detail+'\n注意点\n'+('\n\n'.join(t.warnings) if t.warnings else '読み込み時の注意点はありません。')
        self.details_dialog=DetailsDialog(self,'読み込み情報・注意点',content)

    def refresh_alignment(self):
        if self.trajectory:
            def go():
                self.display_frames=align_frames(self.trajectory.frames) if self.align.get() else self.trajectory.frames
                self.scene_extent=trajectory_extent(self.display_frames)
                self.draw_scene();self.draw_plot()
            self.guard(go)

    def selected(self):
        if not self.trajectory:raise ValueError('先にIRCログまたはXYZを読み込んでください。')
        a,b,n=int(self.start.get()),int(self.end.get()),int(self.stride.get())
        indexes=selection_indices(self.display_frames,a,b,n)
        return [self.display_frames[i] for i in indexes]

    def slider_move(self,v):
        if self.display_frames:self.set_frame(round(float(v)),from_slider=True)
    def tree_select(self,e):
        s=self.tree.selection()
        if s:self.set_frame(int(s[0]))
    def set_frame(self,i,from_slider=False):
        if not self.display_frames:return
        self.index=max(0,min(i,len(self.display_frames)-1));f=self.display_frames[self.index]
        direction={'forward':'Forward','reverse':'Reverse','ts':'TS'}.get(f.branch,f'経路 {f.path}')
        self.frame_label.set(f'位置 {self.index} / {len(self.display_frames)-1}   {direction}・点 {f.point}')
        if not from_slider:self.slider.set(self.index)
        if self.tree.exists(str(self.index)) and self.tree.selection()!=(str(self.index),):
            self.tree.selection_set(str(self.index));self.tree.see(str(self.index))
        self.request_scene_draw();self.draw_plot()
    def toggle_play(self):
        if not self.display_frames:return
        if self.playing:self.stop_path_playback()
        else:self.playing=True;self.play_btn.configure(text='❚❚ 停止');self.tick()
    def stop_path_playback(self):
        self.playing=False;self.play_btn.configure(text='▶ 再生')
        if self.play_job is not None:self.after_cancel(self.play_job);self.play_job=None
    def tick(self):
        self.play_job=None
        if self.playing:
            self.set_frame((self.index+1)%len(self.display_frames));self.play_job=self.after(130,self.tick)
    def begin_scene_drag(self,e):
        self.scene.focus_set()
        if not self.display_frames:self.drag=None;return
        self.drag=dict(start=(e.x,e.y),last=(e.x,e.y),pan=bool(getattr(e,'state',0)&1),moved=False)

    def rotate(self,e):
        if self.drag is None:return
        drag=self.drag
        distance=(e.x-drag['start'][0])**2+(e.y-drag['start'][1])**2
        if not drag['moved'] and distance<16:return
        drag['moved']=True
        dx=e.x-drag['last'][0];dy=e.y-drag['last'][1]
        if drag['pan']:self.pan=(self.pan[0]+dx,self.pan[1]+dy)
        else:self.yaw+=dx*.01;self.pitch+=dy*.01
        drag['last']=(e.x,e.y);self.request_scene_draw()

    def end_scene_drag(self,e):
        drag=self.drag;self.drag=None
        if drag is None:return
        distance=(e.x-drag['start'][0])**2+(e.y-drag['start'][1])**2
        if drag['moved'] or drag['pan'] or distance>=16 or getattr(e,'state',0)&1:return
        self.select_atom_at(e.x,e.y)

    def select_atom_at(self,x,y):
        if self.scene_job is not None:self.draw_scene()
        if self.scene_projection is None or not self.display_frames:return
        atom=hit_test(self.scene_projection,x,y)
        if atom is None:self.picked=[]
        elif atom in self.picked:self.picked.remove(atom)
        elif len(self.picked)==2:self.picked=[atom]
        else:self.picked.append(atom)
        self.draw_scene()

    def clear_picks(self):
        self.picked=[];self.drag=None;self.draw_scene()

    def update_measurement(self):
        if not self.display_frames:self.picked=[]
        if self.display_frames:
            frame=self.display_frames[self.index]
            self.picked=[i for i in self.picked if 0<=i<len(frame.symbols)][:2]
        if len(self.picked)==2:
            first,second=self.picked
            distance=math.dist(frame.coords[first],frame.coords[second])
            text=f'{first+1} {frame.symbols[first]} - {second+1} {frame.symbols[second]}: {distance:.4f} Å'
            self.measurement.set('距離：'+text)
        elif self.picked:
            atom=self.picked[0]
            self.measurement.set(f'距離：{atom+1} {frame.symbols[atom]} → もう1つクリック')
            text=''
        else:self.measurement.set('距離：原子を2つクリック');text=''
        self.clear_measurement_button.configure(state='normal' if self.picked else 'disabled')
        return text

    def scale_view(self,f):
        self.zoom=max(.15,min(8,self.zoom*f));self.request_scene_draw()

    def wheel(self,e):
        delta=getattr(e,'delta',0)
        if not delta:return
        steps=max(-10,min(10,delta/120))
        self.scale_view(1.12**steps)

    def reset_view(self):
        self.yaw=-.45;self.pitch=.65;self.zoom=1.;self.pan=(0.,0.);self.drag=None;self.draw_scene()

    def restore_camera(self,settings):
        def number(key,default):
            try:value=float(settings.get(key,default))
            except (TypeError,ValueError):return default
            return value if math.isfinite(value) else default
        self.yaw=number('yaw',-.45);self.pitch=number('pitch',.65)
        self.zoom=max(.15,min(8,number('zoom',1.)))
        self.pan=(number('pan_x',0.),number('pan_y',0.));self.drag=None

    def request_scene_draw(self):
        if self.scene_job is None:self.scene_job=self.after_idle(self.draw_scene)

    def draw_scene(self):
        if self.scene_job is not None:self.after_cancel(self.scene_job);self.scene_job=None
        c=self.scene;c.delete('all');w=max(1,c.winfo_width());h=max(1,c.winfo_height())
        distance_text=self.update_measurement()
        if not self.display_frames:
            self.scene_image=None;self.scene_photo=None;self.scene_projection=None
            c.create_text(w/2,h/2,text='IRCログを読み込んで経路を確認',width=max(100,w-40),justify='center',fill=MUTED,font=('Yu Gothic UI',16));return
        frame=self.display_frames[self.index]
        try:
            rendered,projection=self.scene_renderer.render(frame.symbols,frame.coords,(w,h),yaw=self.yaw,pitch=self.pitch,zoom=self.zoom,pan=self.pan,labels=self.labels.get(),bonds=self.show_bonds.get(),picked=tuple(self.picked),extent=self.scene_extent)
        except (ValueError,MemoryError) as exc:
            self.scene_image=None;self.scene_photo=None;self.scene_projection=None
            reason=str(exc) or '描画用メモリを確保できませんでした。'
            notice='構造プレビューを表示できません。\n'+reason+'\nウィンドウを小さくするか、視点を戻して再試行してください。'
            c.create_text(w/2,h/2,text=notice,width=max(100,min(w-40,650)),justify='center',fill=MUTED,font=('Yu Gothic UI',11))
            self.status.set('構造プレビュー：'+reason)
            return
        if distance_text and w>120 and h>40:
            draw=ImageDraw.Draw(rendered);font=load_font(14)
            bounds=draw.textbbox((0,0),distance_text,font=font);text_width=bounds[2]-bounds[0]
            text_height=bounds[3]-bounds[1];top=max(0,h-text_height-22)
            draw.rounded_rectangle((7,top,min(w-7,text_width+25),h-7),radius=5,fill='#f2f7fa',outline='#b9cad5')
            draw.text((14,top+6-bounds[1]),distance_text,font=font,fill=INK)
        self.scene_image=rendered;self.scene_projection=projection
        self.scene_photo=ImageTk.PhotoImage(rendered)
        c.create_image(0,0,anchor='nw',image=self.scene_photo)

    def save_scene_png(self):
        def save():
            if not self.display_frames:raise ValueError('先にIRCログまたはXYZを読み込んでください。')
            self.stop_path_playback();self.draw_scene()
            if self.scene_image is None:raise ValueError('構造プレビューを描画できません。ウィンドウの大きさや視点を調整してから保存してください。')
            # Copy before the native file chooser starts its nested event loop.
            # Resizing must not change the image being saved.
            snapshot=self.scene_image.copy();point_index=self.index
            filename=filedialog.asksaveasfilename(parent=self,title='構造プレビューをPNG保存',defaultextension='.png',initialfile=f'irc_structure_{point_index:04d}.png',filetypes=[('PNG画像','*.png')],confirmoverwrite=True)
            if filename:
                snapshot.save(filename,format='PNG')
                self.status.set(f'構造プレビュー（位置 {point_index}）を保存しました：{filename}')
        self.guard(save)

    def plot_xy(self):
        vals=[f.energy for f in self.display_frames]
        if not vals or any(v is None for v in vals):return None
        base=vals[0];yy=np.array([(v-base)*627.509474 for v in vals])
        use_rc=any(f.branch=='ts' for f in self.display_frames) and all(f.reaction_coordinate is not None for f in self.display_frames)
        xx=np.array([f.reaction_coordinate for f in self.display_frames]) if use_rc else np.arange(len(vals))
        xx=xx-xx.min();xspan=max(1e-12,float(xx.max()))
        w=max(120,self.plot.winfo_width());h=max(80,self.plot.winfo_height());span=max(.01,float(yy.max()-yy.min()))
        xy=[(55+float(x)*max(1,w-75)/xspan,h-20-(float(y)-yy.min())/span*(h-46)) for x,y in zip(xx,yy)]
        return xy,yy
    def draw_plot(self):
        c=self.plot;c.delete('all');data=self.plot_xy()
        if data is None:
            c.create_text(16,25,anchor='w',text='エネルギー情報なし',fill=MUTED);return
        xy,yy=data
        c.create_text(10,9,anchor='nw',text='ΔE(SCF) / kcal mol⁻¹（先頭構造基準）',fill=MUTED,font=('Yu Gothic UI',9))
        if len(xy)>1:c.create_line(*[v for p in xy for v in p],fill=TEAL,width=2)
        x,y=xy[self.index];c.create_oval(x-4,y-4,x+4,y+4,fill='#ea9660',outline='white')
        c.create_text(c.winfo_width()-10,9,anchor='ne',text=f'{yy[self.index]:.2f}',fill=INK)
        use_rc=any(f.branch=='ts' for f in self.display_frames) and all(f.reaction_coordinate is not None for f in self.display_frames)
        label='反応座標（開始側 − / 終了側 ＋、ログと同じ単位） →' if use_rc else '経路点の位置番号 →'
        c.create_text(c.winfo_width()-10,c.winfo_height()-5,anchor='se',text=label,fill=MUTED,font=('Yu Gothic UI',8))
        for i,f in enumerate(self.display_frames):
            if f.branch=='ts':
                tx,ty=xy[i];c.create_line(tx,25,tx,c.winfo_height()-20,fill='#c48547',dash=(3,3))
                c.create_text(tx+5,28,anchor='nw',text='TS',fill='#803c12',font=('Segoe UI',9,'bold'))
    def plot_click(self,e):
        data=self.plot_xy()
        if data:self.set_frame(min(range(len(data[0])),key=lambda i:abs(data[0][i][0]-e.x)))

    def pick_exe(self,var):
        p=filedialog.askopenfilename(title='実行ファイルを選択',filetypes=[('実行ファイル','*.exe'),('すべて','*')])
        if p:var.set(p)
    def pick_dir(self,var):
        p=filedialog.askdirectory()
        if p:var.set(p)
    def export_xyz(self):
        frames=self.guard(self.selected)
        if not frames:return
        p=filedialog.asksaveasfilename(defaultextension='.xyz',initialfile='selected_irc.xyz',filetypes=[('XYZ','*.xyz')])
        if p:
            def go():save_xyz(frames,p);self.status.set(f'{len(frames)}構造を書き出しました：{p}')
            self.guard(go)
    def _copy_selected_xyz(self):
        frames=self.selected()
        lines=[]
        for f in frames:
            lines.extend([str(len(f.symbols)),f'path={f.path} point={f.point}'])
            lines.extend(f'{s} {p[0]:.10f} {p[1]:.10f} {p[2]:.10f}' for s,p in zip(f.symbols,f.coords))
        self.clipboard_clear();self.clipboard_append('\n'.join(lines)+'\n');self.update()
        self.status.set(f'{len(frames)}構造をコピーしました。IboViewでCtrl＋Shift＋Vを押してください。')
        return len(frames)

    def copy_xyz(self):
        self.guard(self._copy_selected_xyz)

    def launch_ibo(self):
        def go():
            exe=Path(self.ibo_exe.get().strip())
            if not exe.is_file():raise ValueError('IboViewの実行ファイルを指定してください。')
            self._copy_selected_xyz()
            subprocess.Popen([str(exe.resolve())],cwd=str(exe.resolve().parent))
            self.store_settings()
        self.guard(go)

    def copy_script(self):
        def go():
            if not self.png_dir.get().strip():raise ValueError('PNG保存フォルダーを選んでください。')
            directory=Path(self.png_dir.get()).resolve();directory.mkdir(parents=True,exist_ok=True)
            if any(p.is_file() and p.suffix.lower()=='.png' for p in directory.iterdir()):raise ValueError('PNGがすでにあります。上書きを避けるため、空の保存フォルダーを選んでください。')
            script=make_iboview_script(directory)
            scriptfile=directory/'export_iboview_frames.js';scriptfile.write_text(script,encoding='utf-8')
            self.clipboard_clear();self.clipboard_append(script);self.update()
            self.status.set('保存スクリプトをコピーしました。IBOを選択したIboViewでCtrl＋Shift＋Vを押してください。')
        self.guard(go)


    def project_settings(self):
        return dict(iboview=self.ibo_exe.get(),ffmpeg=self.ffmpeg.get(),analysis_level=self.analysis_level.get(),png_dir=self.png_dir.get(),stride=self.stride.get(),start=self.start.get(),end=self.end.get(),align=self.align.get(),fps=self.fps.get(),kind=self.kind.get(),pingpong=self.pingpong.get(),yaw=self.yaw,pitch=self.pitch,zoom=self.zoom,pan_x=self.pan[0],pan_y=self.pan[1],viewer_labels=self.labels.get(),viewer_bonds=self.show_bonds.get())
    def store_settings(self):
        try:
            CONFIG.parent.mkdir(parents=True,exist_ok=True);CONFIG.write_text(json.dumps(self.project_settings(),ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:pass
    def save_project(self):
        if not self.trajectory:return
        p=filedialog.asksaveasfilename(defaultextension='.json',initialfile='irc_project.json',filetypes=[('IRC IBO Project','*.json')])
        if p:
            def go():save_project(self.trajectory,p,self.project_settings());self.status.set(f'保存しました：{p}')
            self.guard(go)
    def open_project(self):
        if self.busy:return
        p=filedialog.askopenfilename(filetypes=[('IRC IBO Project','*.json')])
        if not p:return
        def go():
            t,s=load_project(p);self.install_trajectory(t)
            mapping={'iboview':self.ibo_exe,'ffmpeg':self.ffmpeg,'analysis_level':self.analysis_level,'png_dir':self.png_dir,'stride':self.stride,'start':self.start,'end':self.end,'align':self.align,'fps':self.fps,'kind':self.kind,'pingpong':self.pingpong,'viewer_labels':self.labels,'viewer_bonds':self.show_bonds}
            for key,var in mapping.items():
                if key in s:var.set(s[key])
            self.restore_camera(s);self.refresh_alignment()
        self.guard(go)

    def load_pngs(self):
        p=filedialog.askdirectory(title='IboViewのPNG連番フォルダー',initialdir=self.png_dir.get() or None)
        if p:self.guard(lambda:self.read_pngs(p))
    def png_directory_changed(self,*_):
        current=self.png_dir.get().strip()
        if not current or str(Path(current).resolve())!=self.preview_dir:
            self.stop_png_playback();self.preview_images=[];self.preview_dir='';self.preview_photo=None
            self.png_info.set('「PNG連番を読み込む」で、保存した画像を選んでください。')
            self.draw_png()
    def read_pngs(self,p):
        if not str(p).strip():raise ValueError('PNG連番フォルダーを選んでください。')
        files=list_frames(p)
        if not files:raise ValueError('PNG連番が見つかりません。')
        self.stop_png_playback();self.preview_images=files;self.preview_dir=str(Path(p).resolve());self.png_index=0;self.png_dir.set(str(p))
        self.png_info.set(f'{len(files)}枚のPNG\n{p}')
        self.png_slider.configure(to=max(1,len(files)-1));self.png_slider.set(0);self.draw_png();self.tabs.select(self.movie_tab)
    def png_slide(self,v):
        if self.preview_images:self.png_index=min(len(self.preview_images)-1,max(0,round(float(v))));self.draw_png()
    def draw_png(self):
        c=self.image_canvas;c.delete('all');w=max(1,c.winfo_width());h=max(1,c.winfo_height())
        if not self.preview_images:c.create_text(w/2,h/2,text='IBOのPNG連番プレビュー',width=max(100,w-40),justify='center',fill=MUTED,font=('Yu Gothic UI',15));return
        try:
            with Image.open(self.preview_images[self.png_index]) as im:
                im=im.convert('RGB');im.thumbnail((max(1,w-24),max(1,h-42)),Image.Resampling.LANCZOS)
                self.preview_photo=ImageTk.PhotoImage(im)
            c.create_image(w/2,h/2,image=self.preview_photo)
            c.create_text(12,h-12,anchor='sw',text=f'{self.png_index+1} / {len(self.preview_images)}  {self.preview_images[self.png_index].name}',fill=INK)
        except Exception as e:
            self.png_playing=False;c.create_text(15,25,anchor='w',text=str(e),fill='#ad4141')
    def toggle_png(self):
        if not self.preview_images:return
        if self.png_playing:self.stop_png_playback()
        else:self.png_playing=True;self.tick_png()
    def stop_png_playback(self):
        self.png_playing=False
        if self.png_job is not None:self.after_cancel(self.png_job);self.png_job=None
    def tick_png(self):
        self.png_job=None
        if self.png_playing:
            self.png_index=(self.png_index+1)%len(self.preview_images);self.png_slider.set(self.png_index)
            try:fps=max(1,min(60,int(self.fps.get())))
            except ValueError:fps=12
            self.png_job=self.after(round(1000/fps),self.tick_png)
    def make_movie(self):
        if self.busy:return
        try:
            if not self.png_dir.get().strip():raise ValueError('先にPNG連番フォルダーを選んでください。')
            self.read_pngs(self.png_dir.get())
        except Exception as e:messagebox.showerror('PNG連番',str(e),parent=self);return
        kind=self.kind.get();p=filedialog.asksaveasfilename(defaultextension='.'+kind,initialfile='irc_ibo.'+kind,filetypes=[(kind.upper(),'*.'+kind)])
        if not p:return
        try:fps=int(self.fps.get())
        except ValueError:messagebox.showerror('FPS','FPSは整数で指定してください。');return
        directory=self.png_dir.get();ping=self.pingpong.get();ffmpeg=self.ffmpeg.get()
        self.stop_png_playback()
        def work():return export_movie(directory,p,fps=fps,pingpong=ping,ffmpeg_path=ffmpeg,kind=kind,progress=lambda s:self.events.put(('progress',s)),cancel_event=self.cancel_event)
        def done(path):
            self.status.set(f'動画を保存しました：{path}');messagebox.showinfo('動画の保存',f'保存しました。\n{path}',parent=self)
        self.run_worker(work,done,cancellable=True)
    def run_worker(self,work,done,cancellable=False):
        if self.busy:messagebox.showinfo('処理中','実行中の処理が終わってから操作してください。');return
        self.busy=True;self.cancel_event.clear();self.worker_done=done;self.cancel_button.configure(state='normal' if cancellable else 'disabled');self.status.set('処理しています…')
        def run():
            try:self.events.put(('done',work()))
            except Exception as e:self.events.put(('error',str(e)))
        threading.Thread(target=run,daemon=True).start()
    def _poll(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='progress':self.status.set(data)
                else:
                    self.busy=False;self.cancel_button.configure(state='disabled')
                    if kind=='done':self.status.set('処理が完了しました。');self.worker_done(data)
                    else:self.status.set(data);messagebox.showerror('処理できませんでした',data,parent=self)
        except queue.Empty:pass
        self.after(100,self._poll)
    def close(self):
        if self.busy:
            messagebox.showinfo('処理中','処理が終わるまでお待ちください。動画出力は「処理を中止」で停止できます。',parent=self);return
        self.stop_path_playback();self.stop_png_playback()
        if self.scene_job is not None:self.after_cancel(self.scene_job);self.scene_job=None
        self.store_settings();self.destroy()

def position_dialog(dialog,parent,width,height):
    """Keep modeless-sized dialogs inside the screen, centered on their parent."""
    dialog.update_idletasks()
    width=min(width,dialog.winfo_screenwidth()-60)
    height=min(height,dialog.winfo_screenheight()-100)
    x=max(0,min(parent.winfo_rootx()+(parent.winfo_width()-width)//2,dialog.winfo_screenwidth()-width))
    y=max(0,min(parent.winfo_rooty()+(parent.winfo_height()-height)//2,dialog.winfo_screenheight()-height-50))
    dialog.geometry(f'{width}x{height}+{x}+{y}')


class DetailsDialog(tk.Toplevel):
    """Scrollable, selectable diagnostics instead of an oversized message box."""
    def __init__(self,app,title,text):
        super().__init__(app)
        self.title(title);self.transient(app);self.configure(bg=BG)
        self.minsize(520,300)
        box=ttk.Frame(self,padding=14);box.pack(fill='both',expand=True)
        ttk.Label(box,text=title,font=('Yu Gothic UI',14,'bold')).pack(anchor='w',pady=(0,10))
        buttons=ttk.Frame(box);buttons.pack(side='bottom',fill='x',pady=(10,0))
        ttk.Button(buttons,text='閉じる',command=self.destroy).pack(side='right')
        body=ttk.Frame(box);body.pack(fill='both',expand=True)
        detail=tk.Text(body,wrap='word',width=1,height=1,bg='white',fg=INK,font=('Yu Gothic UI',10),relief='solid',borderwidth=1,padx=10,pady=10,selectbackground=TEAL,selectforeground='white')
        scroll=ttk.Scrollbar(body,command=detail.yview);scroll.pack(side='right',fill='y')
        detail.configure(yscrollcommand=scroll.set);detail.pack(side='left',fill='both',expand=True)
        detail.insert('1.0',text);detail.configure(state='disabled')
        self.bind('<Escape>',lambda e:self.destroy())
        position_dialog(self,app,780,480);self.grab_set()


class MergeDialog(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app)
        self.app=app;self.title('Forward ＋ Reverseの統合');self.transient(app)
        self.resizable(True,True);self.minsize(600,340)
        self.configure(bg=BG)
        defaults=getattr(app,'merge_defaults',{})
        self.forward=tk.StringVar(value=defaults.get('forward',''))
        self.reverse=tk.StringVar(value=defaults.get('reverse',''))
        self.start_side=tk.StringVar(value=defaults.get('start_side','reverse'))
        buttons=ttk.Frame(self,padding=(14,8));buttons.pack(side='bottom',fill='x')
        ttk.Button(buttons,text='キャンセル',command=self.destroy).pack(side='right')
        self.submit_button=ttk.Button(buttons,text='統合して読み込む',style='Accent.TButton',command=self.submit)
        self.submit_button.pack(side='right',padx=8)
        scroll=ScrollFrame(self,padding=(14,14,8,0));scroll.pack(fill='both',expand=True)
        box=scroll.content
        app._wrapped(box,'同じTSから計算した、2つのIRCログを選択',font=('Yu Gothic UI',14,'bold')).pack(fill='x',pady=(0,8))
        for name,var in [('Forward',self.forward),('Reverse',self.reverse)]:
            app._field(box,name+' のIRCログ',var,browse=lambda v=var,n=name:self.pick(v,n))
        order=ttk.LabelFrame(box,text='経路の並べ方',padding=10);order.pack(fill='x',pady=12)
        ttk.Radiobutton(order,text='Reverse終点 → TS → Forward終点',variable=self.start_side,value='reverse').pack(anchor='w')
        ttk.Radiobutton(order,text='Forward終点 → TS → Reverse終点',variable=self.start_side,value='forward').pack(anchor='w')
        app._wrapped(box,'Forwardが生成物側とは限りません。統合後、両端の構造を確認してください。\nTSの重複と補正途中の構造は除外します。座標の向きは経路画面でそろえられます。',foreground=MUTED).pack(fill='x',pady=(0,8))
        self.bind('<Escape>',lambda e:self.destroy())
        position_dialog(self,app,780,500);self.grab_set()

    def pick(self,var,name):
        p=filedialog.askopenfilename(parent=self,title=name+'のIRCログ',filetypes=[('Gaussian IRC','*.log *.LOG *.out *.OUT'),('すべて','*')])
        if p:var.set(p)

    def submit(self):
        if self.app.busy:return
        forward=self.forward.get().strip();reverse=self.reverse.get().strip();start=self.start_side.get()
        if not forward or not reverse:
            messagebox.showerror('IRCログの選択','ForwardとReverseを両方選択してください。',parent=self);return
        self.app.merge_defaults=dict(forward=forward,reverse=reverse,start_side=start)
        self.app.run_worker(lambda:merge_irc_logs(forward,reverse,start),self.app.install_trajectory)
        self.destroy()

def main():
    app=App();app.mainloop()

if __name__=='__main__':main()
