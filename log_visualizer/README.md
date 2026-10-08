# log_visualizer

大規模 C/C++ プロジェクトのログとソースコードを突き合わせ、実際の実行フローを**シーケンス図（単一 HTML）**で可視化するツールです。
出力 HTML はブラウザだけで閲覧でき、追加インストールやネットワーク接続は不要です（JS・CSS・Graphviz（viz.js）をすべて HTML に埋め込みます）。

設計の詳細は [docs/architecture.md](docs/architecture.md) を参照してください。

## 必要環境

| 項目 | 内容 |
|---|---|
| Python | 3.11 以上（3.14 で動作確認） |
| libclang | 任意。`pip install libclang` で導入（LLVM の別途インストールは不要）。無い場合は正規表現ベースの簡易解析に自動で切り替わる |
| ブラウザ | Chrome / Edge / Firefox の最新版（`DecompressionStream` を使用） |

## セットアップ

`LogAnalyze` ディレクトリ（`log_visualizer` の親）で実行します。

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r log_visualizer/requirements.txt
```

## 実行手順

```bash
# 基本: ログ + ソースダンプ → lv_output/ に出力
python -m log_visualizer --log 参考資料/log.txt --source 参考資料/source_dump.txt --out lv_output

# サンプルで試す
python -m log_visualizer --log log_visualizer/sample/sample.log --source log_visualizer/sample/sample_source_dump.txt --out lv_sample
```

`lv_output/sequence.html` をブラウザで開きます。

### 出力ファイル

| ファイル | 内容 |
|---|---|
| `sequence.html` | シーケンス図ビューア（単一 HTML） |
| `callgraph.json` | 要件形式の隣接リスト `{"caller": ["callee", ...]}` |
| `callgraph_detail.json` | 関数定義（ファイル・行範囲・モジュール）と辺（種別・行・経由情報） |
| `callgraph.dot` | Graphviz 形式（同期=実線 / 非同期=点線 / コールバック=破線） |

関数 ID は `<ファイル名>::<関数名>`（例 `ingress_task1.c::handle_submit`）です。
同名ファイルが複数ある場合は、重複しなくなるまで親ディレクトリを付けます（例 `MiniPrinter/AutoRequestGenerator_Config.c::f`）。
解析できない関数ポインタ呼び出しは `UNRESOLVED_FUNCTION_POINTER` への辺として残ります。

### 主なオプション

| オプション | 説明 |
|---|---|
| `--source PATH` | ソースダンプ、またはソースのディレクトリ。省略するとログだけで可視化（矢印なし） |
| `--backend auto\|clang\|regex` | ソース解析方式（既定 auto: libclang があれば clang） |
| `--jobs N` | clang 解析の並列数（既定: CPU 数 − 1） |
| `--no-cache` / `--cache-dir DIR` | 解析キャッシュ（既定 `.lv_cache/`）を使わない / 置き場所を変える |
| `--from` / `--to` | 時刻範囲で事前に絞り込む（`2026-10-07 14:37:15.000` 形式） |
| `--module NAME` | 指定モジュールのログだけを出力（複数指定可） |
| `--max-entries N` | 先頭 N 件だけ処理 |
| `--embed-sources logged\|all\|none` | ソースビューアに埋め込むソース（既定 logged: ログに出現したファイル） |
| `--config FILE` | 設定ファイル（TOML）。既定設定のうち指定した項目だけ上書き |

### 処理時間の目安（実データ: ソース 1,819 ファイル）

| 処理 | 時間 |
|---|---|
| libclang 解析（初回） | 約 2〜6 分（並列。以降はキャッシュで数秒） |
| 正規表現解析 | 約 8 秒 |
| ログ 100 万行の解析〜HTML 出力 | 約 75 秒、Python 側のピークメモリ約 60MB（出力 HTML 約 25MB） |

## 画面の使い方

**シーケンス図タブ**

- 横軸はモジュール、縦軸は時間（上が古いログ）です。各ボックスには関数名とログ本文が表示されます。
- 矢印の線種: 実線=同期呼び出し、点線=非同期通知（キュー・イベント・スレッド生成）、破線=コールバック。
- マウスホイールで拡大・縮小します。Shift+ホイールまたはドラッグでスクロールします。
- ボックス左上の ▾ をクリックすると、その呼び出し先のログを折り畳みます（キーボードの ← / → でも操作可能）。
- 左ペインでモジュール・関数・ログレベル・ログ種別・時刻範囲を絞り込めます。thread_id の絞り込み欄は、ログにスレッド情報がある場合だけ表示されます。
- 上部の検索欄で、全文・関数名・メッセージを検索できます（Enter で次の一致、Shift+Enter で前の一致）。
- ボックスをクリックすると、右ペインに全文・ファイル名・行番号・呼び出し元候補・呼び出し先候補を表示します。
- ファイル名のリンクを押すとソースビューアが開き、該当行がハイライトされます。

**CallGraph タブ**

- 関数を検索して選ぶと、その周辺 N ホップの CallGraph を Graphviz で描画します。ノードをクリックすると、その関数を中心に描き直します。
- 描画に使った DOT テキストを右ペインからコピーできます。

## 設定（config/default_config.toml）

| セクション | 内容 |
|---|---|
| `[source]` | ダンプのパス接頭辞の除去、対象拡張子、除外パターン（CMakeFiles・テストコードなど） |
| `[analyzer]` | 解析方式、並列数、コンパイル引数、`-D` マクロ、キャッシュ先、CallGraph から除外する関数（`ut_log_*` など） |
| `[[async_apis]]` | 非同期 API の定義。`thread`（エントリ関数の引数位置）、`queue_send` / `queue_receive`（キューの引数位置）、`event_send`（送信先タスクハンドルの引数位置）、`callback_register` を指定 |
| `[modules]` | モジュール決定ルール。① ログのモジュール欄（`PCL:xxx` → `PCL`）② ファイル名ルール ③ ディレクトリルール、および別名（例: `conversionLayer.*` を 1 レーンにまとめる） |
| `[sequence]` | 呼び出し元を探す最大ホップ数、時間窓（同期・非同期・コールバックそれぞれ） |
| `[render]` | ソースの埋め込み方針、メッセージの最大長 |

## 開発

`log_visualizer` ディレクトリで実行します。

```bash
python -m pytest          # テスト
python -m mypy .          # 型チェック（strict）
python -m flake8          # Lint
```

サンプルは `sample/sample_src/` を編集し、`python sample/make_sample.py` で `sample.log` と `sample_source_dump.txt` を再生成します。

## 既知の制約

- 対象ログ（`utility_log_console.c` の書式）の `[0]` はスレッド ID ではありません。そのため、このログではスレッドの色分けは行いません。
- 関数ポインタの呼び出し先は、構造体フィールドへの代入（`.on_done = fn` など）の名前一致でのみ解決します。テーブル経由の間接呼び出しなどは `UNRESOLVED_FUNCTION_POINTER` になります。
- 矢印は、CallGraph 上の呼び出し元のうち、時間窓内で最も新しいログに結びます。ログ出力のない中間関数は、最大ホップ数の範囲でたどります。推定なので、誤って結線することがあります。
- 100 万件規模の HTML は、ブラウザ側で数百 MB のメモリを使います。必要に応じて `--from` / `--to` / `--module` で絞り込んでください。
