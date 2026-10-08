"""シーケンス生成テスト（矢印・深度・モジュール決定）。"""

from __future__ import annotations

from pathlib import Path

from log_visualizer.analyzer.module_resolver import ModuleResolver
from log_visualizer.analyzer.sequence_builder import SequenceBuilder
from log_visualizer.config.settings import FileRule, ModuleSettings, Settings
from log_visualizer.domain.models import (
    CallKind,
    LogEntry,
    LogKind,
    LogLevel,
    SequenceEvent,
    SourceFile,
)
from log_visualizer.parser.log_parser import LogParser
from log_visualizer.parser.source_dump import SourceDumpReader

from .conftest import build_graph, log_line

FILES = [
    SourceFile(
        "src/ingress/ingress_task1.c",
        "\n".join(
            [
                "static void handle_submit(int id)",  # 1
                "{",
                "    create_task(id);",
                "}",
            ]
        ),
    ),
    SourceFile(
        "src/job/task.c",
        "\n".join(
            [
                "int create_task(int id)",  # 1
                "{",
                "    execute_task(id);",
                "    return id;",
                "}",
                "static void execute_task(int id)",  # 6
                "{",
                "    OsMsgQSend(s_ctx.jobQueue, &id, 4, 0, 0);",
                "}",
                "static void worker(void *arg)",  # 10
                "{",
                "    OsMsgQReceive(s_ctx.jobQueue, &id, 4, 0);",
                "    run_job(id);",
                "}",
                "static void run_job(int id) { }",  # 15
            ]
        ),
    ),
]


def make_builder(settings: Settings) -> SequenceBuilder:
    graph = build_graph(settings, FILES)
    return SequenceBuilder(graph, ModuleResolver(settings.modules), settings.sequence)


def events_for(settings: Settings, lines: list[str]) -> list[SequenceEvent]:
    return list(make_builder(settings).build(LogParser().parse_lines(lines)))


def test_arrows_follow_callgraph(settings: Settings) -> None:
    lines = [
        log_line("14:37:15.300", "INFO", "PCL:handle_submit()", "begin", "ingress_task1.c", 3, "handle_submit"),
        log_line("14:37:15.310", "INFO", "JobManager", "create", "task.c", 3, "create_task"),
        log_line("14:37:15.320", "INFO", "JobManager", "execute", "task.c", 8, "execute_task"),
        log_line("14:37:15.330", "INFO", "JobManager", "run", "task.c", 15, "run_job"),
        log_line("14:37:15.340", "INFO", "PCL:handle_submit()", "queued", "ingress_task1.c", 3, "handle_submit"),
    ]
    ev = events_for(settings, lines)
    assert [e.lane for e in ev] == ["PCL", "JobManager", "JobManager", "JobManager", "PCL"]
    assert ev[0].function_id == "ingress_task1.c::handle_submit"
    assert ev[0].parent_seq is None and ev[0].depth == 0
    assert ev[1].parent_seq == 0 and ev[1].arrow_kind is CallKind.SYNC and ev[1].depth == 1
    assert ev[2].parent_seq == 1 and ev[2].arrow_kind is CallKind.SYNC and ev[2].depth == 2
    # execute_task --(queue)--> worker --> run_job : 非同期（2 ホップ）
    assert ev[3].parent_seq == 2 and ev[3].arrow_kind is CallKind.ASYNC and ev[3].hops == 2
    # 同じ関数の後続ログは兄弟（矢印なし）
    assert ev[4].arrow_kind is None and ev[4].parent_seq is None and ev[4].depth == 0


def test_no_arrow_when_caller_logged_later_or_outside_window(settings: Settings) -> None:
    lines = [
        log_line("14:37:15.310", "INFO", "JobManager", "create", "task.c", 3, "create_task"),
        log_line("14:37:15.320", "INFO", "PCL:x", "begin", "ingress_task1.c", 3, "handle_submit"),
        log_line("14:37:20.000", "INFO", "JobManager", "create", "task.c", 3, "create_task"),
    ]
    ev = events_for(settings, lines)
    # A.timestamp <= B.timestamp を満たさない（呼び出し元ログが後）
    assert ev[0].parent_seq is None
    # 同期の時間窓（1 秒）を超えている
    assert ev[2].parent_seq is None


def test_unresolved_function_falls_back_to_log_module(settings: Settings) -> None:
    ev = events_for(settings, [log_line("14:00:00.000", "INFO", "netif", "up", "Netif.c", 1, "NetifUp")])
    assert ev[0].function_id is None and ev[0].lane == "netif"


def _entry(module: str | None, file_name: str | None, kind: LogKind = LogKind.STRUCTURED) -> LogEntry:
    return LogEntry(
        seq=0, line_no=1, timestamp=None, timestamp_ms=None, thread_id=None, tick=None,
        log_level=LogLevel.INFO, module_name=module, function_name=None, message="",
        file_name=file_name, line_number=None, kind=kind,
    )


def test_module_priority() -> None:
    resolver = ModuleResolver(
        ModuleSettings(
            prefix_separators=(":",),
            file_rules=(FileRule("eif_*.c", "EIF"),),
            directory_rules=(r"^src/([^/]+)/",),
        )
    )
    # 1. ログの PCL:xxx プレフィックス
    assert resolver.lane_for(_entry("PCL:handle_submit()", "eif_x.c"), None) == "PCL"
    # 2. ファイル名
    assert resolver.lane_for(_entry(None, "eif_notify.c"), None) == "EIF"
    # 3. ディレクトリ名
    assert resolver.module_for_path("src/job/task.c") == "job"
    assert resolver.lane_for(_entry(None, None, LogKind.RAW), None) == "(other)"


def test_sample_end_to_end_sequence(settings: Settings, sample_log: Path, sample_dump: Path) -> None:
    repo = SourceDumpReader(sample_dump, settings.source)
    graph = build_graph(settings, repo.iter_files())
    builder = SequenceBuilder(graph, ModuleResolver(settings.modules), settings.sequence)
    events = list(builder.build(LogParser().parse(sample_log)))
    by_seq = {e.entry.seq: e for e in events}

    def parent_func(e: SequenceEvent) -> str | None:
        return by_seq[e.parent_seq].entry.function_name if e.parent_seq is not None else None

    on_done = next(e for e in events if e.entry.function_name == "on_task_done")
    assert parent_func(on_done) == "process_job" and on_done.arrow_kind is CallKind.CALLBACK
    handled = next(e for e in events if e.entry.function_name == "eif_handle_event")
    assert parent_func(handled) == "eif_notify_completion" and handled.arrow_kind is CallKind.ASYNC
    dispatch = next(e for e in events if e.entry.function_name == "ingress_task1_main")
    assert parent_func(dispatch) == "ExternalRequestIngressSubmit"
    assert dispatch.arrow_kind is CallKind.ASYNC
    lanes = {e.lane for e in events}
    assert {"PCL", "JobManager", "EIF", "RIM", "(other)"} <= lanes
