"""ログ解析テスト。"""

from __future__ import annotations

import types
from datetime import datetime
from pathlib import Path

from log_visualizer.domain.models import LogEntry, LogKind, LogLevel
from log_visualizer.parser.log_parser import LogParser

from .conftest import log_line


def parse(lines: list[str]) -> list[LogEntry]:
    return list(LogParser().parse_lines(lines))


def test_structured_line_fields() -> None:
    line = (
        "[2026-10-07 14:37:15.343] [0] INFO  PCL:handle_submit()     task1=submit task2 "
        "queued generation_id=2 (ingress_task1.c:348 handle_submit)"
    )
    (entry,) = parse([line])
    assert entry.timestamp == datetime(2026, 10, 7, 14, 37, 15, 343000)
    assert entry.thread_id is None  # [0] は thread_id ではない（tick）
    assert entry.tick == 0
    assert entry.log_level is LogLevel.INFO
    assert entry.module_name == "PCL:handle_submit()"
    assert entry.function_name == "handle_submit"
    assert entry.message == "task1=submit task2 queued generation_id=2"
    assert entry.file_name == "ingress_task1.c"
    assert entry.line_number == 348
    assert entry.kind is LogKind.STRUCTURED


def test_module_with_spaces_and_truncation() -> None:
    lines = [
        log_line("14:37:14.245", "INFO", "PCL:API request Start", "api=submit begin", "ingress_api.c", 19, "Submit"),
        log_line("14:37:14.245", "INFO", "conversionLayer.callbac", "invoke", "cb.c", 1, "Invoke"),
        log_line("14:37:14.245", "WARN", "OperationBlock", "reply to upper", "ope.c", 46, "Notify"),
    ]
    entries = parse(lines)
    assert [e.module_name for e in entries] == [
        "PCL:API request Start",
        "conversionLayer.callbac",
        "OperationBlock",
    ]
    assert entries[0].message == "api=submit begin"
    assert entries[2].log_level is LogLevel.WARN


def test_message_containing_parentheses() -> None:
    line = log_line(
        "14:37:14.599", "INFO", "PCL:pipeline_mainte",
        "submit_to_use_case_block(use_case_request_id=1, completion=bridge_completion)",
        "exe_pipeline.c", 180, "exe_pipeline_submit_print_head_maintenance",
    )
    (entry,) = parse([line])
    assert entry.message.startswith("submit_to_use_case_block(")
    assert entry.line_number == 180


def test_interleaved_trace_is_split_and_record_is_repaired() -> None:
    """別出力の割り込みで末尾 ')' が次行に回ったレコードを修復する（log.txt 134-135 行目の再現）。"""
    lines = [
        "[2026-10-07 14:37:15.343] [0] INFO  PCL:API Result          runtime=submit response status=0 "
        "(ingress_runtime.c:226 ingress_runtime_submit*****************RIM_PRODUCT_STATE_ERROR_BUSY"
        "[BuildProductStateCapability(89)]",
        "[2026-10-07 14:37:15.629] )",
        log_line("14:37:15.630", "INFO", "arg_module", "next", "a.c", 1, "f"),
    ]
    entries = parse(lines)
    assert len(entries) == 3
    first, trace, nxt = entries
    assert first.function_name == "ingress_runtime_submit"
    assert first.line_number == 226
    assert first.message == "runtime=submit response status=0"
    # 割り込み TRACE は中断されたレコードの後に出る
    assert trace.kind is LogKind.TRACE
    assert trace.function_name == "BuildProductStateCapability"
    assert trace.line_number == 89
    assert trace.message == "RIM_PRODUCT_STATE_ERROR_BUSY"
    assert nxt.function_name == "f"
    assert [e.seq for e in entries] == [0, 1, 2]


def test_split_function_name_continuation() -> None:
    """関数名の途中で割り込まれたケース（log.txt 306-307 行目の再現）。"""
    lines = [
        "[2026-10-07 14:40:17.947] [0] INFO  arg_module              Received ID from queue: 0 "
        "(AutoRequestGenerator_Worker.c:58 AutoRequestGenerator_HandleNextQue"
        "*****************Adapter to store DataID=6[Dispatch(51)]",
        "[2026-10-07 14:40:17.957] uedRequest)",
    ]
    first, trace = parse(lines)
    assert first.function_name == "AutoRequestGenerator_HandleNextQueuedRequest"
    assert first.timestamp is not None and first.timestamp.second == 17
    assert trace.function_name == "Dispatch"


def test_multiline_message_trailer_on_next_line() -> None:
    lines = [
        "[2026-10-07 14:37:14.631] [0] INFO  RIM                     *****************list.items[index]=1 "
        "[function_execution_pipeline_status_update(58)]",
        "[2026-10-07 14:37:14.631]  (function_execution_pipeline_status.c:58 "
        "function_execution_pipeline_status_update)",
    ]
    (entry,) = parse(lines)
    assert entry.kind is LogKind.STRUCTURED
    assert entry.module_name == "RIM"
    assert entry.file_name == "function_execution_pipeline_status.c"
    assert entry.function_name == "function_execution_pipeline_status_update"
    assert "list.items[index]=1" in entry.message


def test_trace_and_raw_lines() -> None:
    lines = [
        "[2026-10-07 14:36:57.089] =================================",
        "[2026-10-07 14:37:07.146] *****************Adapter to store DataID=1[Dispatch(51)]",
        "[2026-10-07 14:37:07.162] ",
        "[2026-10-07 14:37:07.162] thread '<unnamed>' (538) panicked at src/server.rs:164:29:",
    ]
    entries = parse(lines)
    assert [e.kind for e in entries] == [LogKind.RAW, LogKind.TRACE, LogKind.RAW]
    assert entries[1].function_name == "Dispatch" and entries[1].line_number == 51
    assert entries[2].message.startswith("thread '<unnamed>'")


def test_structured_without_trailer() -> None:
    (entry,) = parse([log_line("14:00:00.000", "ERROR", "netif", "link down")])
    assert entry.function_name is None
    assert entry.log_level is LogLevel.ERROR
    assert entry.message == "link down"


def test_parse_is_generator_and_streams(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    with path.open("w", encoding="utf-8", newline="\r\n") as fp:
        for i in range(5000):
            fp.write(log_line("14:00:00.000", "INFO", "m", f"msg {i}", "a.c", i + 1, "f") + "\n")
    result = LogParser().parse(path)
    assert isinstance(result, types.GeneratorType)
    first = next(result)
    assert first.message == "msg 0"
    assert sum(1 for _ in result) == 4999


def test_sample_log(sample_log: Path) -> None:
    entries = list(LogParser().parse(sample_log))
    kinds = {e.kind for e in entries}
    assert kinds == {LogKind.STRUCTURED, LogKind.TRACE, LogKind.RAW}
    repaired = [e for e in entries if e.function_name == "ExternalRequestIngressSubmit"]
    assert len(repaired) == 6  # 割り込みのある行も含めて 3 要求 × 2
