# log_visualizer システムアーキテクチャ設計書

対象: 大規模 C/C++ プロジェクト（数千〜数万ファイル）の実行ログ（数十万〜数百万行）
目的: ログとソースコードを突き合わせ、実際の実行フローをシーケンス図として単一 HTML で可視化する。

---

## 1. 前提と設計判断

実データ（`参考資料/log.txt`, `参考資料/source_dump.txt`）の調査結果と、要件確認で決定した事項。

| # | 事実 / 決定事項 | 設計への反映 |
|---|---|---|
| D1 | ログの `[0]` は `ut_log_write_decorated_file` の `record->timestamp`（常に 0）であり thread_id ではない | `LogEntry.thread_id` は `None`。`[N]` は `tick` として保持。スレッド色分けは**行わない**（決定）。thread_id を出力する書式が将来追加された場合に備え、フィルタ・色分けの経路は残す |
| D2 | モジュール欄は `%-23s` 固定幅。空白を含む（`PCL:API request Start`）・23 文字で切り詰め（`conversionLayer.callbac`） | 空白分割せず正規表現で抽出。別名マップを設定ファイルで提供 |
| D3 | 別出力（RIM の `*****...[func(line)]`、Rust panic）が**行の途中に割り込み**、1 レコードが複数物理行に分断される | 物理行 → 論理レコードの「組み立て器（Assembler）」を独立させる |
| D4 | `*****...[Dispatch(51)]` 形式（ファイル名なし）や素の printf 行が混在 | `LogKind`（STRUCTURED / TRACE / RAW）で区別し、捨てない |
| D5 | 同名 static 関数が別ファイルに存在（`handle_submit` × 3） | 関数 ID = `<一意な最短パス末尾>::<関数名>`（例 `ingress_task1.c::handle_submit`）（決定） |
| D6 | ソースファイル名の重複が 41 件 | 上記 ID のパス部は「重複しない最短の末尾」に自動延長（例 `Config/FaultManager_Config.c::f`） |
| D7 | タイムスタンプ分解能 ≒ 15ms。同一時刻が多数 | 並び順は `(timestamp, seq)`。矢印は「直近の呼び出し元ログ 1 件」に限定 |
| D8 | 非同期は独自 OS 抽象化（`OsTaskCreate` 99 / `OsEventSend` 81 / `OsMsgQSend` 78 …） | 非同期 API を**設定ファイル化**。キュー・イベントは引数の対応付けで静的に結線 |
| D9 | ソース解析: libclang（pip 版）＋ 簡易解析の自動切替（決定） | `SourceAnalyzer` ポートに 2 実装。pip 版 libclang は標準ヘッダを持たないため**スタブヘッダを自動生成** |
| D10 | 閲覧環境はオフライン（決定） | JS/CSS/viz.js（Graphviz WASM）をすべて HTML にインライン化 |
| D11 | ソースダンプに CMake の CompilerId・テストコードが含まれる | 除外パターンを設定ファイルで指定（既定で除外） |

---

## 2. システム構成図

```
                ┌──────────────────────── CLI (__main__.py / cli.py) ────────────────────────┐
                │  引数解析 → Settings 読込 → AnalyzeUseCase 実行                                │
                └──────────────────────────────────┬────────────────────────────────────────┘
                                                   │
┌──────────────────────────── application ─────────▼───────────────────────────────────────┐
│ AnalyzeUseCase                                                                            │
│   1. SourceDumpReader ──(Iterator[SourceFile])──► SourceAnalyzer ──► CallGraphBuilder       │
│   2. CallGraph ──► CallGraphExporter (callgraph.json / callgraph.dot)                      │
│   3. LogReader ──(Iterator[LogEntry])──► SequenceBuilder ──(Iterator[SequenceEvent])──►    │
│   4. HtmlRenderer（ストリーミング書込み）──► sequence.html                                   │
└───────────────────────────────────────────────────────────────────────────────────────────┘
        │ 依存方向（外 → 内）: cli → application → (parser, analyzer, callgraph, renderer) → domain

 ┌─ parser ───────────────┐ ┌─ analyzer ────────────────────┐ ┌─ callgraph ─────────────┐
 │ PhysicalLineReader     │ │ ClangSourceAnalyzer (libclang)│ │ FunctionIdAllocator     │
 │ LogRecordAssembler     │ │ RegexSourceAnalyzer (fallback)│ │ CallGraphBuilder        │
 │ UtLogGrammar           │ │ AnalyzerFactory (auto 切替)   │ │ CallGraphExporter       │
 │ LogParser (generator)  │ │ AsyncApiRules                 │ │   json / dot            │
 │ SourceDumpReader       │ │ ModuleResolver                │ └─────────────────────────┘
 └────────────────────────┘ │ LogFunctionResolver           │ ┌─ renderer ──────────────┐
                            │ SequenceBuilder               │ │ PayloadWriter (gzip)    │
 ┌─ domain ───────────────┐ └───────────────────────────────┘ │ HtmlRenderer            │
 │ models.py  (dataclass) │                                    └────────────┬────────────┘
 │ ports.py   (Protocol)  │                                                 │ inline
 └────────────────────────┘                                    ┌─ ui ───────▼────────────┐
                                                               │ template.html / app.css │
                                                               │ app.js (Canvas 描画)    │
                                                               │ vendor/viz-standalone.js│
                                                               └─────────────────────────┘
```

### データフロー（メモリ効率の要点）

```
log.txt ──1行ずつ──► PhysicalLineReader ──► LogRecordAssembler ──► LogParser ──► LogEntry (generator)
                                                                                   │
                     SequenceBuilder（呼び出し元の直近ログだけを dict で保持: O(関数数)）◄─┘
                                   │ SequenceEvent (generator)
                                   ▼
                     PayloadWriter: 1 イベント = JSON 1 行 → gzip ストリーム（一時ファイル）
                                   ▼
                     HtmlRenderer: template 前半 → base64 をチャンク出力 → template 後半
```

- ログは**一括ロードしない**。全段がジェネレータで連結され、Python 側で全件をリスト化しない。
- 文字列（モジュール名・関数名・ファイル名）はインターン表で整数化し、行データは整数中心の配列にする。
- ソースダンプも `SourceFile` 単位でストリーム処理する（15MB / 42 万行でも 1 ファイル分のみ保持）。
- 重いソース解析の結果は、ダンプの SHA-256 と設定のハッシュをキーにキャッシュする（`.lv_cache/`）。

---

## 3. ディレクトリ構成

```
LogAnalyze/
└── log_visualizer/
    ├── __init__.py
    ├── __main__.py              # python -m log_visualizer
    ├── cli.py                   # 引数解析・エントリポイント
    ├── requirements.txt
    ├── pyproject.toml           # mypy / pytest 設定
    ├── setup.cfg                # flake8 設定
    ├── README.md                # 実行手順
    ├── docs/architecture.md     # 本書
    ├── config/                  # 設定（モジュール別名・非同期 API・除外パス）
    │   ├── settings.py
    │   └── default_config.toml
    ├── domain/                  # エンティティ・値オブジェクト・ポート（外部依存なし）
    │   ├── models.py
    │   └── ports.py
    ├── application/             # ユースケース（処理の組立て）
    │   └── analyze_usecase.py
    ├── parser/                  # ログ / ソースダンプの読込み
    │   ├── log_grammar.py
    │   ├── log_parser.py
    │   └── source_dump.py
    ├── analyzer/                # ソース解析・モジュール決定・シーケンス生成
    │   ├── raw_model.py         # 解析器の共通出力（ファイル単位・名前ベース）
    │   ├── text_utils.py        # コメント除去・#if 処理・呼出し/参照の抽出
    │   ├── body_extractor.py    # 関数本体から呼出し・非同期 API・関数参照を抽出
    │   ├── async_rules.py
    │   ├── regex_analyzer.py
    │   ├── clang_analyzer.py
    │   ├── analyzer_factory.py  # 解析方式の選択＋キャッシュ
    │   ├── module_resolver.py
    │   ├── function_resolver.py
    │   └── sequence_builder.py
    ├── callgraph/
    │   ├── function_id.py
    │   ├── builder.py
    │   ├── provider.py          # 解析→構築をまとめた CallGraphProvider 実装
    │   └── exporter.py
    ├── renderer/
    │   ├── payload_writer.py
    │   └── html_renderer.py
    ├── ui/
    │   ├── template.html
    │   ├── app.css
    │   ├── app.js
    │   └── vendor/viz-standalone.js (+ LICENSE)
    ├── tests/
    └── sample/
        ├── sample_src/          # サンプル C プロジェクト（ダンプの元）
        ├── make_sample.py       # sample.log / sample_source_dump.txt の生成
        ├── sample.log
        ├── sample_source_dump.txt
        └── output/              # サンプル出力（HTML / callgraph.json / .dot）
```

クリーンアーキテクチャとの対応:

| 層 | パッケージ | 依存してよい先 |
|---|---|---|
| Entities | `domain` | なし（標準ライブラリのみ） |
| Use Cases | `application` | `domain`（ポート経由で外側を利用） |
| Interface Adapters | `parser`, `analyzer`, `callgraph`, `renderer` | `domain`, `config` |
| Frameworks & Drivers | `cli`, `ui`, libclang | 全て |

`application` は具象クラスを直接 import せず、`domain.ports` の Protocol を受け取る（`cli.py` が組み立てて注入する）。

---

## 4. データモデル定義（domain/models.py）

```python
class LogLevel(str, Enum):   TRACE / DEBUG / INFO / WARN / ERROR / FATAL / UNKNOWN
class LogKind(str, Enum):    STRUCTURED   # ut_log 形式（モジュール欄あり）
                             TRACE        # *****...[func(line)] 形式
                             RAW          # 上記以外（バナー・panic 等）

@dataclass(frozen=True, slots=True)
class LogEntry:
    seq: int                    # 論理レコード番号（0 起点・出現順）
    line_no: int                # 開始物理行番号（1 起点）
    timestamp: datetime | None
    timestamp_ms: int | None    # epoch ミリ秒（比較・描画用）
    thread_id: str | None       # このログ形式では常に None（D1）
    tick: int | None            # [N] の値
    log_level: LogLevel
    module_name: str | None     # ログに書かれたモジュール欄（生値）
    function_name: str | None
    message: str
    file_name: str | None
    line_number: int | None
    kind: LogKind
    raw: str                    # 組立て後の生テキスト

class CallKind(str, Enum):  SYNC（実線） / ASYNC（点線） / CALLBACK（破線）

@dataclass(frozen=True, slots=True)
class FunctionInfo:
    function_id: str            # "ingress_task1.c::handle_submit"
    name: str                   # "handle_submit"（C++ は "Class::method"）
    file_path: str              # ダンプ内の正規化パス（"/" 区切り, "Input/" 除去）
    start_line: int
    end_line: int
    is_static: bool
    module: str                 # ディレクトリから決定したモジュール

@dataclass(frozen=True, slots=True)
class CallEdge:
    caller: str                 # function_id
    callee: str                 # function_id または UNRESOLVED_FUNCTION_POINTER
    kind: CallKind
    line: int                   # 呼び出し箇所の行
    via: str | None             # "OsMsgQSend:requestQueue" / "field:on_complete" など

class CallGraph:                # 隣接リスト（順方向・逆方向）＋ FunctionInfo 辞書
    functions: dict[str, FunctionInfo]
    callees(fid) / callers(fid) / edges() / to_adjacency()

@dataclass(frozen=True, slots=True)
class SequenceEvent:
    entry: LogEntry
    lane: str                   # 横軸モジュール
    function_id: str | None     # 解決できたソース上の関数
    parent_seq: int | None      # 矢印の始点（呼び出し元ログ）
    arrow_kind: CallKind | None
    depth: int                  # 呼び出し深度（折り畳み用）
    hops: int                   # 呼び出し元までの CallGraph 上の距離
```

---

## 5. クラス設計

### 5.1 parser

| クラス | 責務 |
|---|---|
| `PhysicalLineReader` | ファイルを 1 行ずつ読み、`(line_no, text)` を yield。CRLF/エンコーディング吸収 |
| `UtLogGrammar` | 1 行の分類用正規表現群（記録開始行 / 末尾 `(file:line func)` / TRACE 行 / 割り込み検出）。別形式を追加するときはこのクラスを差し替える |
| `LogRecordAssembler` | 物理行 → 論理レコード。(1) 割り込み TRACE 片を切り出し、中断されたレコードの**後**に出す、(2) 末尾が途中で切れたレコードに後続行を連結、(3) 末尾だけの行（RIM の改行入りメッセージ）を直前レコードに連結 |
| `LogParser` | 論理レコード → `LogEntry`。`parse(path) -> Iterator[LogEntry]` |
| `SourceDumpReader` | `===== FILE BEGIN/CONTENT BEGIN/CONTENT END/FILE END =====` 形式を逐次読込み `Iterator[SourceFile]`。除外パターン適用 |

### 5.2 analyzer

| クラス | 責務 |
|---|---|
| `RawFileAnalysis`（raw_model） | 1 ファイルの解析結果（関数定義・名前ベースの呼び出し・関数参照・フィールド代入・非同期 API 呼出し）。2 つの解析器の共通出力 |
| `ClangSourceAnalyzer` | ダンプを作業ディレクトリへ展開、スタブ標準ヘッダとプレリュードを生成、全ヘッダディレクトリを `-I` 指定して libclang で解析。`multiprocessing` で並列化 |
| `RegexSourceAnalyzer` | コメント/文字列除去 → `#if` 第 1 分岐のみ残す → 波括弧追跡で関数本体を特定 → 呼出し抽出。libclang 不在時の代替 |
| `AnalyzerFactory` | `backend = auto/clang/regex`。auto は libclang の import 可否で選択 |
| `AsyncApiRules` | 設定から非同期 API を読み、`thread`（エントリ関数引数）/`queue_send`/`queue_receive`/`event_send`/`callback_register` を判定 |
| `ModuleResolver` | 優先順位: ① ログのモジュール欄（`PCL:xxx` → `PCL`、別名適用）② ファイル名ルール ③ ディレクトリルール |
| `LogFunctionResolver` | `LogEntry` → `function_id`。(file, func) → 同名複数なら**ログ行番号を含む定義範囲**で絞込み → 関数名のみ |
| `SequenceBuilder` | ストリーミングで矢印・深度を決定（§6） |

### 5.3 callgraph

| クラス | 責務 |
|---|---|
| `FunctionIdAllocator` | ファイル名重複を考慮した最短一意パス末尾で ID を払い出す |
| `CallGraphBuilder` | 名前ベースの呼出しを ID に解決（同一ファイル → 非 static で一意 → パスが最も近い定義）。関数ポインタ呼出しはフィールド名で登録箇所と照合し、解決不能なら `UNRESOLVED_FUNCTION_POINTER`。`exclude_callees`（既定 `ut_log_*`）に一致する呼出し先は除外（clang はログマクロを展開するため全関数に辺が付くのを防ぐ） |
| `CallGraphExporter` | `callgraph.json`（要件形式）, `callgraph_detail.json`（種別・行・経由情報）, `callgraph.dot` |

### 5.4 renderer / ui

| 要素 | 責務 |
|---|---|
| `PayloadWriter` | イベントを NDJSON で gzip 一時ファイルへ逐次書込み。文字列はインターン |
| `HtmlRenderer` | template に CSS/JS/viz.js をインライン化し、payload を base64 でチャンク出力 |
| `app.js` | gzip 展開（`DecompressionStream`）→ Canvas でシーケンス図を描画（表示範囲のみ）。フィルタ・検索・折り畳み・ホイールズーム・詳細ペイン・ソースビューア・CallGraph タブ（viz.js で DOT 描画） |

---

## 6. 矢印生成アルゴリズム（SequenceBuilder）

```
for B in entries (時刻順・ストリーム):
    fB = LogFunctionResolver(B)
    cand = reverse_reach(fB, max_hops)        # CallGraph を逆向きに BFS（結果はキャッシュ）
                                              # → {caller_fid: (hops, path_kind)}
    best = argmax over caller in cand of recent[caller].seq
           where B.ts - recent[caller].ts <= window[path_kind]
    if best:  arrow(best → B, kind=path_kind), depth = best.depth + 1
    elif recent[fB] exists (同じ関数の連続ログ): parent/depth を継承（矢印なし）
    else: depth = 0
    recent[fB] = B
```

- 経路種別 `path_kind`: 経路中に CALLBACK 辺があれば CALLBACK、ASYNC 辺があれば ASYNC、それ以外 SYNC。
- 条件 `A.timestamp <= B.timestamp` はストリーム順で自動的に満たされる。
- 時間窓（同期 1 秒 / 非同期・コールバック 10 秒、設定可）で誤結線を抑える。
- メモリ: `recent` は関数数に比例。ログ件数に依存しない。

### 非同期の静的結線（CallGraphBuilder）

| 種別 | 例 | 結線方法 |
|---|---|---|
| thread | `OsTaskCreate(&s_task, Entry, &attr)` / `pthread_create(&t, NULL, fn, arg)` / `std::thread(fn)` | 呼出し元 → エントリ関数（ASYNC）。ハンドル `s_task` → Entry を記録 |
| event | `OsEventSend(s_task, EV)` | ハンドル表から送信先タスクのエントリ関数へ ASYNC |
| queue | `OsMsgQSend(ctx.requestQueue, …)` ↔ `OsMsgQReceive(ctx.requestQueue, …)` | キー（末尾メンバ名）一致の送信関数 → 受信関数へ ASYNC。複数候補時は同一ディレクトリ優先 |
| callback | `.on_complete = fn` / `register_cb(fn)` / `x->on_complete(...)` | 登録関数 → fn を CALLBACK。フィールド経由呼出しはフィールド名で fn に解決 |

---

## 7. UI 設計（単一 HTML・オフライン）

```
┌──────────────┬───────────────────────────────────────────┬──────────────────┐
│ フィルタ      │ [シーケンス図] [CallGraph]   検索[____][全文▼] │ ログ詳細          │
│ ・モジュール  │ PCL │ arg_module │ RIM │ OperationBlock …   │ 全文             │
│ ・関数        │ ┌──────────┐                                │ ファイル:行 (link)│
│ ・thread_id※ │ │handle_.. │──────►┌────────┐              │ 関数 ID           │
│ ・ログレベル  │ └──────────┘       │create..│              │ 呼び出し元候補    │
│ ・時刻範囲    │      ┊ (async)     └────────┘              │ 呼び出し先候補    │
│              │      ▼                                     │──────────────────│
│              │                                            │ ソースビューア     │
└──────────────┴───────────────────────────────────────────┴──────────────────┘
※ thread_id はデータに存在する場合のみ表示
```

- 描画は Canvas。可視範囲を二分探索し、その範囲のボックス・矢印のみ描く（100 万件でも描画コストは画面内件数に比例）。
- マウスホイール: 拡大縮小（カーソル位置基準）。Shift+ホイール / ドラッグ: スクロール。
- 折り畳み: 子孫を持つボックスの ▾ をクリックで子孫を非表示。
- 矢印: 同期=実線、非同期=点線、コールバック=破線。
- CallGraph タブ: 関数を選び、N ホップの部分グラフを DOT 生成 → viz.js で SVG 描画。DOT テキストもコピー可能。

---

## 8. 性能目標と制約

| 項目 | 目標 / 方針 |
|---|---|
| ログ 100 万行の解析 | 逐次処理。Python 側メモリはログ件数にほぼ依存しない（payload は gzip 一時ファイル） |
| ソース 2,000 ファイル | libclang 並列解析（数分）→ 2 回目以降はキャッシュで数秒 |
| HTML サイズ | payload は gzip。ソースはログに出現したファイルのみ埋込み（既定） |
| ブラウザ | Chrome / Edge / Firefox 最新版（`DecompressionStream` 必須） |
| 制約 | 100 万件を全件ブラウザに載せると数百 MB のメモリを要する。必要に応じて CLI の `--from/--to/--module` で事前に絞り込む |

---

## 9. 実装時に追加した判断

| 事象 | 対応 |
|---|---|
| clang はマクロを展開するため、`UT_LOG_*` 経由でほぼ全関数に `ut_log_write_default` への辺が付く | `analyzer.exclude_callees` で除外（既定 `ut_log_*`） |
| TRACE 行はディレクトリから `rim`、構造化ログは `RIM` となり別レーンに分かれる | 大文字小文字だけが違うレーンは統合（Python 側で可能な範囲＋UI 側で最終統合） |
| `f(a, fn, b)` の引数を位置指定初期化子と誤認し、スレッド生成がコールバック扱いになる | 位置指定初期化子 `{ fn_a, fn_b }` の抽出はファイルスコープに限定 |
| キャッシュキーに並列数や backend 指定値（auto/clang）が入り、無駄に再解析される | 解析結果に影響する設定（source / async_apis / コンパイル引数）だけでキーを作る |
| 実測（実データ） | libclang 初回 2〜6 分 → 2 回目以降 1〜2 秒。ログ 100 万行: 75 秒・ピーク 61MB・HTML 25MB。ブラウザで 97 万件を約 2 秒で表示・検索 |

---

## 10. 拡張ポイント

- 別のログ書式: `LogGrammar` Protocol を実装して `LogParser` に渡す。
- 別の解析器（tree-sitter 等）: `SourceAnalyzer` Protocol を実装して `AnalyzerFactory` に登録。
- 非同期 API・モジュール別名・除外パス: `config/default_config.toml` を複製して `--config` で指定。
- 別の出力（JSON のみ / Mermaid 等）: `domain.ports.SequenceRenderer` を実装。
