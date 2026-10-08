"""CallGraph 生成テスト。"""

from __future__ import annotations

import json
from pathlib import Path

from log_visualizer.callgraph.exporter import CallGraphExporter
from log_visualizer.callgraph.function_id import split_function_id, unique_suffixes
from log_visualizer.config.settings import Settings
from log_visualizer.domain.models import (
    UNRESOLVED_FUNCTION_POINTER,
    CallEdge,
    CallKind,
    SourceFile,
)
from log_visualizer.parser.source_dump import SourceDumpReader

from .conftest import build_graph

FILES = [
    SourceFile(
        "src/ingress/ingress_task1.c",
        """
static void handle_submit(int id) { create_task(id); }
static void dispatch(int id) { handle_submit(id); }
""",
    ),
    SourceFile(
        "src/exec/function_execution_task2.c",
        """
static void handle_submit(int id) { submit_to_pipeline(id); }
static void submit_to_pipeline(int id) { }
""",
    ),
    SourceFile(
        "src/job/task.c",
        """
#include "utility_log.h"
int create_task(int id) { UT_LOG_INFO("JobManager", "x"); execute_task(id); return id; }
static void execute_task(int id) { ut_log_write_default(1); }
void unknown_ptr(Ctx *c) { (*c->fp)(); }
""",
    ),
    SourceFile("src/log/utility_log.c", "int ut_log_write_default(int x) { return x; }\n"),
    SourceFile("src/a/config.c", "void f(void) {}\n"),
    SourceFile("src/b/config.c", "void g(void) {}\n"),
]


def test_unique_suffixes() -> None:
    result = unique_suffixes(["src/a/config.c", "src/b/config.c", "src/x/main.c"])
    assert result == {
        "src/a/config.c": "a/config.c",
        "src/b/config.c": "b/config.c",
        "src/x/main.c": "main.c",
    }
    assert split_function_id("a/config.c::Klass::method") == ("a/config.c", "Klass::method")


def test_static_functions_with_same_name_are_distinct(settings: Settings) -> None:
    graph = build_graph(settings, FILES)
    assert "ingress_task1.c::handle_submit" in graph.functions
    assert "function_execution_task2.c::handle_submit" in graph.functions
    adj = graph.to_adjacency()
    assert adj["ingress_task1.c::handle_submit"] == ["task.c::create_task"]
    assert adj["function_execution_task2.c::handle_submit"] == [
        "function_execution_task2.c::submit_to_pipeline"
    ]
    assert adj["ingress_task1.c::dispatch"] == ["ingress_task1.c::handle_submit"]
    assert adj["task.c::create_task"] == ["task.c::execute_task"]
    assert "a/config.c::f" in graph.functions and "b/config.c::g" in graph.functions


def test_requirement_example_chain(settings: Settings) -> None:
    """要件例: handle_submit └─ create_task └─ execute_task"""
    graph = build_graph(settings, FILES)
    first = graph.callees("ingress_task1.c::handle_submit")[0]
    second = graph.callees(first.callee)[0]
    assert (first.callee, second.callee) == ("task.c::create_task", "task.c::execute_task")


def test_unresolved_function_pointer_and_excluded_logger(settings: Settings) -> None:
    graph = build_graph(settings, FILES)
    adj = graph.to_adjacency()
    assert adj["task.c::unknown_ptr"] == [UNRESOLVED_FUNCTION_POINTER]
    # ut_log_* は exclude_callees で除外
    assert "task.c::execute_task" not in adj


def test_async_and_callback_edges_on_sample(settings: Settings, sample_dump: Path) -> None:
    repo = SourceDumpReader(sample_dump, settings.source)
    graph = build_graph(settings, repo.iter_files())

    def edge(caller: str, callee: str) -> CallEdge:
        return next(e for e in graph.callees(caller) if e.callee == callee)

    queue = edge("ingress_api.c::ExternalRequestIngressSubmit", "ingress_task1.c::ingress_task1_main")
    assert queue.kind is CallKind.ASYNC and queue.via == "OsMsgQSend:requestQueue"
    thread = edge("task_manager.c::execute_task", "task_manager.c::task_worker")
    assert thread.kind is CallKind.ASYNC and thread.via == "pthread_create"
    event = edge("eif_notify.c::eif_notify_completion", "eif_notify.c::eif_task_main")
    assert event.kind is CallKind.ASYNC and event.via == "OsEventSend:s_eif_task"
    callback = edge("task_manager.c::process_job", "ingress_task1.c::on_task_done")
    assert callback.kind is CallKind.CALLBACK and callback.via == "field:on_done"
    assert any(e.callee == UNRESOLVED_FUNCTION_POINTER for e in graph.callees("task_manager.c::process_job"))
    assert graph.functions["ingress_task1.c::handle_submit"].module == "ingress"


def test_export_files(settings: Settings, tmp_path: Path) -> None:
    graph = build_graph(settings, FILES)
    paths = CallGraphExporter().write(graph, tmp_path)
    assert [p.name for p in paths] == ["callgraph.json", "callgraph_detail.json", "callgraph.dot"]
    simple = json.loads((tmp_path / "callgraph.json").read_text(encoding="utf-8"))
    assert simple["task.c::create_task"] == ["task.c::execute_task"]
    detail = json.loads((tmp_path / "callgraph_detail.json").read_text(encoding="utf-8"))
    assert detail["functions"]["task.c::create_task"]["module"] == "job"
    dot = (tmp_path / "callgraph.dot").read_text(encoding="utf-8")
    assert dot.startswith("digraph callgraph {")
    assert '"task.c::create_task" -> "task.c::execute_task" [style=solid' in dot
    assert "UNRESOLVED" in dot
