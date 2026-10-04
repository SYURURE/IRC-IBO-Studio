# Third-party notices / 第三者の権利表示

確認日：2026-10-04。本書は権利者による許諾本文を変更するものではありません。
Studio独自部分のライセンスはGPL-3.0-only。第三者の名称・ロゴ・成果への権利や公認を主張しません。

## IboView

Original author: Gerald Knizia. Copyright (c) 2015 Gerald Knizia.
- 公式配布元：https://www.iboview.org/
- 参照したソースのフォーク：https://github.com/KoehnLab/iboview
- 著作権・GPL v3指定：https://github.com/KoehnLab/iboview/blob/main/iboview-license.txt
- 保存した権利表示：licenses/IboView-COPYRIGHT.txt
- GPL v3全文：LICENSE

Studioは独立した非公式支援ツールです。原作者、KoehnLab、Gaussian社の公式製品・公認製品ではありません。
IboViewの実行ファイル、ライブラリ、基底データ、ロゴ、公式画面の画像はこのZIPに同梱していません。
利用者はIboViewを配布元から別途導入し、実際に取得した版の利用条件を確認してください。
フォーク内の旧READMEにある開発・配布への要望と、明示されたGPL v3のライセンス本文は区別します。
このZIPはIboView本体の再配布許可を独自に付与するものではありません。

### PNG保存スクリプトの由来

irc_ibo_studio/media.py の make_iboview_script が出力するスクリプトは、次の公式サンプルを参考・改変しています。
https://github.com/KoehnLab/iboview/blob/main/example-scripts/save_images_00_frames_of_what_you_see.js
改変点：出力先の絶対パス指定と文字列エスケープ、案内コメント。周辺処理として空フォルダー確認と動画作成を追加。
この由来を独自実装と表示せず、原作者の表示とGPL-3.0-only表示を生成スクリプトにも入れています。
スクリプトや改変版を再配布する場合も、該当するGPL条件・出典・権利表示を保持してください。
他のIboView操作名・API名は互換操作の説明に用い、IboView本体の権利取得を意味しません。

## 実行時に別途導入する依存ソフト

このソースZIPは以下のパッケージ、wheel、実行ファイルを内包しません。
セットアップ時にrequirements.txtの範囲で利用者の仮想環境へインストールします。
範囲指定のため、実際の導入版のライセンス・同梱ライブラリの表示を確認してください。

| ソフト | 主なライセンス・参照先 | 用途 |
|---|---|---|
| Python / Tcl/Tk | Python PSFおよび各構成要素の条件。https://docs.python.org/3/license.html | 実行・GUI |
| NumPy | BSD-3-Clauseを主とする。https://numpy.org/doc/stable/license.html | 座標・数値処理 |
| Pillow | 現行はMIT-CMU。旧版ではPIL/HPND等の表記があるため導入版LICENSEを参照。https://pillow.readthedocs.io/en/stable/about.html | 画像処理 |
| imageio-ffmpeg | PythonラッパーはBSD-2-Clause。https://github.com/imageio/imageio-ffmpeg/blob/main/LICENSE | FFmpeg検出・取得 |
| FFmpeg | ビルド条件によりLGPL/GPL等。https://ffmpeg.org/legal.html | 外部プロセスによるMP4出力 |

imageio-ffmpegのwheelにはFFmpeg実行ファイルが含まれる場合があります。
ラッパーのBSDライセンスをFFmpeg本体に適用してはいけません。
本ZIPはその実行ファイルを再配布しません。将来exe化・仮想環境同梱をする場合は、
使用した各バイナリに対応するライセンス、権利表示、必要な対応ソースの提供を別途整えてください。

## PDF内のフォント

同梱のA4ワークフローPDFはNoto Sans JPをサブセット埋め込みしています。
フォントの権利表示は元フォントnameテーブルから採取しました：
(c) 2014-2021 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'.
SIL Open Font License 1.1。全文・出典は licenses/NotoSansJP-OFL-1.1.txt を参照。
フォントそのものをStudio独自部分のGPLへ変更するものではありません。
フォント配布元：https://github.com/google/fonts/tree/main/ofl/notosansjp

## データ・画像と科学的引用

従来の実計算ログ・XYZと、それに由来するプレビュー画像を配布物から除外しました。
新しい人工サンプルと図は examples/README.md の生成方法によります。
参考文献はREFERENCES.mdを参照。論文の引用はソフトのライセンス条件とは別です。
Gaussianという名称は対応入力形式を説明する目的で使用します。Gaussian本体は含みません。

## 文書の生成元

PDFの編集可能な生成スクリプトをtools/build_workflow_pdf.pyに含めます。
生成時のみReportLab（BSD系）とfonttools（MIT）を使用します。これらの配布物は同梱しません。
取得先と手順はtools/README.mdを参照してください。
