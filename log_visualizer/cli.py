"""コマンドラインエントリポイント（部品の組み立てもここで行う）。"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .analyzer.function_resolver import LogFunctionResolver
from .analyzer.module_resolver import ModuleResolver
from .analyzer.sequence_builder import SequenceBuilder
from .application import AnalyzeRequest, AnalyzeUseCase
from .callgraph.exporter import CallGraphExporter
from .callgraph.provider import EmptyCallGraphProvider, SourceCallGraphProvider
from .config.settings import Settings, load_settings
from .domain.models import CallGraph
from .domain.ports import CallGraphProvider, SourceRepository
from .parser.log_parser import LogParser
from .parser.source_dump import EmptySourceRepository, SourceDumpReader
from .parser.source_tree import DirectorySourceReader
from .renderer.html_renderer import HtmlRenderer

LOG = logging.getLogger("log_visualizer")


def _parse_datetime(text: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"日時の形式が不正です: {text}（例: 2026-10-07 14:37:15.343）")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m log_visualizer",
        description="ログとソースコードを解析し、実行フローをシーケンス図（単一 HTML）で可視化する。",
    )
    p.add_argument("--log", type=Path, help="ログファイル（例: log.txt）。省略するとダイアログで選択")
    p.add_argument(
        "--source",
        type=Path,
        help="ソースのフォルダ（サブフォルダも再帰的に解析）またはソースダンプ（.txt）。"
        "省略時はログのみで可視化",
    )
    p.add_argument("--out", type=Path, help="出力ディレクトリ（既定: lv_output）")
    p.add_argument(
        "--gui",
        action="store_true",
        help="ログ・ソースフォルダ・出力先をエクスプローラーのダイアログで選ぶ（--log 省略時も同様）",
    )
    p.add_argument("--html", default="sequence.html", help="HTML ファイル名（既定: sequence.html）")
    p.add_argument("--config", type=Path, help="設定ファイル（TOML）。既定設定を上書きする")
    p.add_argument("--backend", choices=["auto", "clang", "regex"], help="ソース解析方式（既定: 設定値 auto）")
    p.add_argument("--jobs", type=int, help="clang 解析の並列数")
    p.add_argument("--no-cache", action="store_true", help="解析キャッシュを使わない（読み書きしない）")
    p.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="解析キャッシュを作り直す（ヘッダだけを変更した場合などに使う）",
    )
    p.add_argument("--cache-dir", type=Path, help="キャッシュディレクトリ（既定: .lv_cache）")
    p.add_argument("--embed-sources", choices=["logged", "all", "none"], help="ソースビューアに埋め込むソース")
    p.add_argument("--title", help="HTML のタイトル")
    p.add_argument("--from", dest="time_from", type=_parse_datetime, help="この時刻以降のログのみ（YYYY-MM-DD HH:MM:SS[.mmm]）")
    p.add_argument("--to", dest="time_to", type=_parse_datetime, help="この時刻以前のログのみ")
    p.add_argument("--module", action="append", default=[], help="指定モジュールのログのみ（複数指定可）")
    p.add_argument("--max-entries", type=int, help="先頭から指定件数のみ処理")
    p.add_argument("-v", "--verbose", action="store_true", help="詳細ログ")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    analyzer = settings.analyzer
    if args.backend:
        analyzer = dataclasses.replace(analyzer, backend=args.backend)
    if args.jobs is not None:
        analyzer = dataclasses.replace(analyzer, jobs=args.jobs)
    if args.cache_dir is not None:
        analyzer = dataclasses.replace(analyzer, cache_dir=str(args.cache_dir))
    render = settings.render
    if args.embed_sources:
        render = dataclasses.replace(render, embed_sources=args.embed_sources)
    return dataclasses.replace(settings, analyzer=analyzer, render=render)


def _make_repository(settings: Settings, source: Optional[Path]) -> SourceRepository:
    if source is None:
        return EmptySourceRepository()
    if source.is_dir():
        return DirectorySourceReader(source, settings.source)
    return SourceDumpReader(source, settings.source)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    use_gui = args.gui or args.log is None
    dialogs = None
    if use_gui:
        from .gui import TkDialogs, select_inputs

        try:
            dialogs = TkDialogs()
        except Exception as exc:  # GUI が使えない環境（SSH 等）
            LOG.error("ダイアログを表示できません（%s）。--log を指定してください。", exc)
            return 2
        selection = select_inputs(dialogs, args.log, args.source, args.out)
        if selection is None:
            LOG.info("ログファイルが選択されなかったため終了します")
            return 0
        args.log, args.source, args.out = selection.log, selection.source, selection.out
        LOG.info("ログ: %s", args.log)
        LOG.info("ソース: %s", args.source or "（なし）")
        LOG.info("出力先: %s", args.out)
    if args.out is None:
        args.out = Path("lv_output")

    code = _run(args)
    if dialogs is not None:
        html = args.out / args.html
        if code == 0:
            webbrowser.open(html.resolve().as_uri())
            dialogs.info("完了", f"出力しました:\n{html.resolve()}")
        else:
            dialogs.error("失敗", "処理に失敗しました。コンソールのメッセージを確認してください。")
    return code


def _run(args: argparse.Namespace) -> int:
    if not args.log.exists():
        LOG.error("ログファイルがありません: %s", args.log)
        return 2
    if args.source is not None and not args.source.exists():
        LOG.error("ソースがありません: %s", args.source)
        return 2

    settings = _apply_overrides(load_settings(args.config), args)
    repository = _make_repository(settings, args.source)
    provider: CallGraphProvider
    if args.source is None:
        provider = EmptyCallGraphProvider()
    else:
        provider = SourceCallGraphProvider(
            settings, repository, use_cache=not args.no_cache, rebuild_cache=args.rebuild_cache
        )
    modules = ModuleResolver(settings.modules)

    def sequence_factory(graph: CallGraph) -> SequenceBuilder:
        return SequenceBuilder(graph, modules, settings.sequence, LogFunctionResolver(graph))

    usecase = AnalyzeUseCase(
        log_reader=LogParser(encoding=settings.log.encoding, errors=settings.log.errors),
        sources=repository,
        callgraph_provider=provider,
        sequence_factory=sequence_factory,
        renderer=HtmlRenderer(settings.render),
        callgraph_writer=CallGraphExporter(),
    )
    request = AnalyzeRequest(
        log_path=args.log,
        out_dir=args.out,
        html_name=args.html,
        title=args.title,
        time_from=args.time_from,
        time_to=args.time_to,
        modules=frozenset(args.module),
        max_entries=args.max_entries,
    )

    meta = {
        "log_file": args.log.name,
        "source": args.source.name if args.source else None,
        "tool_version": __version__,
    }
    try:
        usecase.execute(request, meta=meta)
    except KeyboardInterrupt:
        LOG.error("中断しました")
        return 130
    except Exception as exc:  # CLI 境界でまとめて報告
        LOG.error("失敗しました: %s", exc, exc_info=args.verbose)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
