# 開発用ツール

- `python tools/generate_demo.py`：人工サンプルを再生成。標準ライブラリのみ。
- `python tools/build_workflow_pdf.py`：同梱のA4ワークフローPDFの編集可能な生成元。PDFは従来のv0.3.1表記を維持しています。

PDF生成時だけreportlabとfonttoolsが必要です（通常起動の依存には追加しません）。
フォントは https://github.com/google/fonts/tree/main/ofl/notosansjp からNoto Sans JPの可変フォントを取得して
`pdf_assets/NotoSansJP.ttf` として置きます。初回にウェイト400の静的フォントを生成します。
埋込フォントのライセンスはlicenses/NotoSansJP-OFL-1.1.txt。フォント本体はこのZIPに含めません。
ReportLab：https://www.reportlab.com/ （BSD系、使用版のLICENSEを確認）。
fonttools：https://github.com/fonttools/fonttools （MIT、使用版のLICENSEを確認）。
同梱画像は人工サンプルをirc_ibo_studio.viewer.SceneRendererで描画したものです。
