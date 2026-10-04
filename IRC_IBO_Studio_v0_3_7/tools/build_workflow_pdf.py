from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from fontTools.ttLib import TTFont as FTFont
from fontTools.varLib.instancer import instantiateVariableFont

base=Path(__file__).resolve().parents[1]
out=base
fontfile=base/'pdf_assets/NotoSansJP-Regular.ttf'
if not fontfile.exists():
    font=FTFont(base/'pdf_assets/NotoSansJP.ttf')
    instantiateVariableFont(font,{'wght':400},inplace=True);font.save(fontfile)
pdfmetrics.registerFont(TTFont('JP',str(fontfile)))
path=out/'IRC_IBO_Studio_Workflow_A4.pdf'
c=canvas.Canvas(str(path),pagesize=A4)
c.setTitle('IRC IBO Studio | はじめてのIBO可視化');c.setAuthor('IRC IBO Studio')
W,H=A4;left=36;width=W-72;y=H-35
ink=HexColor('#183342');teal=HexColor('#087f8c');muted=HexColor('#526d7b')
def text(t,x,y,size=10,color=ink):
    c.setFont('JP',size);c.setFillColor(color);c.drawString(x,y,t)
def para(t,x,y,w,size=9.8,leading=15,color=ink):
    st=ParagraphStyle('body',fontName='JP',fontSize=size,leading=leading,textColor=color,wordWrap='CJK')
    p=Paragraph(t,st);_,h=p.wrap(w,1000);p.drawOn(c,x,y-h);return h
text('IRC IBO Studio',left,y,21);text('START GUIDE / v0.3.1',W-204,y+2,9,teal);y-=25
text('環境準備から、IRC経路のIBO可視化まで',left,y,13);y-=21
y-=para('Windows 64-bit向け / 初回はインターネット接続が必要。計算済みのIRCログ（.log / .out）または複数構造のXYZを用意します。',left,y,width,9.1,13)
y-=13
blocks=[
('01','PythonとIboViewを用意する',
 'Python公式サイトから「Python install manager」をインストール。新しく開いたPowerShellで下のコマンドを順番に実行し、Tkの小窓が開けば閉じます。<br/>'
 '<font color="#087f8c">py install 3.13　→　py -3.13 -m tkinter</font><br/>'
 'IboView公式サイトからWindows版を取得し、ZIPならフォルダーごと展開。iboview.exeを一度起動して確認します。'),
('02','Studioを展開して初回起動する',
 'IRC_IBO_Studio_v0_3_1.zipを右クリックして「すべて展開」。初回は<b>01_setup.pyw</b>をダブルクリックし、準備完了を待ちます。以後は<b>02_start.pyw</b>をダブルクリックして起動します。<br/>'
 '<font color="#087f8c">.pywの開き方（初回のみ）</font><br/>'
 'ファイルを右クリック →「プログラムから開く」→「別のプログラムを選択」→ <b>Python または Python Launcher</b> →「常に使う」。エディターが開く場合も同じ手順で変更します。Pythonが候補にない場合は、導入済みか確認してください。'),
('03','「1 経路」で読み込み・範囲選択',
 '「IRC / XYZを開く」で読み込み。別々のForward / Reverseログは「Forward ＋ Reverseを統合」を使い、両端の構造を確認します。開始・終了・間隔（位置番号は0開始）を指定。必要なら「重原子で向きをそろえる」をオンにします。まず少数点で動作確認すると安心です。'),
('04','「2 IboView」から渡し、IBOを計算',
 'iboview.exeを指定し「構造をコピー ＋ 起動」。IboViewで<b>Ctrl＋Shift＋V</b>を押します。続いて<b>Ctrl＋Enter</b>で電荷・Extra Spin・汎関数・基底を確認。Run Hartree-Fock/Kohn-ShamとRun Chemical Analysis（IAO/IBO）をオンにして計算します。<br/>'
 '<font color="#526d7b">XYZは座標です。電荷・スピンや波動関数は引き継がれません。Extra Spinは多重度そのものではありません。計算条件は対象系に合わせて設定してください。</font>'),
('05','IboViewで反応に関わる軌道を表示',
 'Data Setsの軌道一覧やView → Find Actively Reacting OrbitalsでIBOを選択し、色と視点を調整します。<b>Ctrl＋T</b>で選んだ軌道を全フレームに描画。Data Sets → Framesで経路に沿った結合の変化を確認できます。'),
('06','PNG連番を保存し、「3 動画」で仕上げる',
 'Studioの「2 IboView」で空のPNG保存フォルダーを選び「PNG保存スクリプトをコピー」。IBO表示中のIboViewで<b>Ctrl＋Shift＋V</b>を押し、保存完了を待ちます。「3 動画」で同じフォルダーを読み込み、FPS・MP4 / GIF・往復再生を指定して書き出します。'),
]
for number,title,body in blocks:
    c.setFillColor(teal);c.roundRect(left,y-20,28,21,4,fill=1,stroke=0)
    text(number,left+7,y-14,10,white);text(title,left+39,y-14,11)
    y-=27;y-=para(body,left+39,y,width-39);y-=13
footer='メモリ不足時：他のアプリを閉じ、IboViewのRequired Memoryを確認。基底を小さくすると計算条件も変わります。少数点で確認後に点数を増やしてください。IRC再生速度は反応の実時間ではありません。'
c.setFillColor(HexColor('#edf5f6'))
measurement=Paragraph(footer,ParagraphStyle('measure',fontName='JP',fontSize=8.6,leading=12,wordWrap='CJK'))
_,h=measurement.wrap(width-20,1000)
c.roundRect(left,y-h-16,width,h+14,5,fill=1,stroke=0)
para(footer,left+10,y-7,width-20,8.6,12);y-=h+25
links=[('Python導入','https://www.python.org/downloads/'),('Windows公式手順','https://docs.python.org/3/using/windows.html'),('IboView公式','https://www.iboview.org/'),('IboView操作ガイド','https://www.iboview.org/_bldUPAt.html')]
xs=[left,left+105,left+245,left+350]
for (label,url),x in zip(links,xs):
    text(label,x,y,8,teal);c.linkURL(url,(x,y-3,x+pdfmetrics.stringWidth(label,'JP',8),y+10),relative=0)
text('公式手順参照：2026-10-04 / 起動トラブルの確認方法は同梱README_ja.mdへ',left,23,7.5,muted)
assert y>42,(y,'page overflow')
c.showPage();c.save();print(path, 'content bottom',round(y,1))
