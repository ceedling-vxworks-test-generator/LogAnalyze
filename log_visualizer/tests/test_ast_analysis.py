"""ソース（AST）解析テスト: 正規表現解析器と libclang 解析器。"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from log_visualizer.analyzer.analyzer_factory import CachedSourceAnalysis, resolve_backend
from log_visualizer.analyzer.clang_analyzer import libclang_available
from log_visualizer.analyzer.raw_model import CALL_DIRECT, CALL_FIELD, CALL_POINTER
from log_visualizer.analyzer.text_utils import blank_preprocessor, strip_comments_and_literals
from log_visualizer.config.settings import Settings
from log_visualizer.domain.models import SourceFile
from log_visualizer.parser.source_dump import SourceDumpReader

from .conftest import analyze_regex

C_SOURCE = """\
#include "os_api.h"
/* コメント内の fake_call(1) は無視される */
static const Ops k_ops = { .on_done = on_done_cb };

static int helper(int x) { return x + 1; }

#if 0
void dead_code(void) { never_called(); }
#else
void live_code(void) { helper(2); }
#endif

void run(Ctx *ctx, void (*cb)(int))
{
    const char *s = "string_call(3)";
    helper(1);
    ctx->ops->on_done(4);
    (*ctx->fp)(5);
    cb(6);
    OsMsgQSend(ctx->requestQueue, &msg, sizeof(msg), 0, 0);
    OsTaskCreate(&s_task, worker_main, &attr);
    register_callback(on_event);
    if (x) { helper(7); }
}
"""

CPP_SOURCE = """\
namespace app {
class Engine {
public:
    void start() { run(); }
    void run();
};
void Engine::run() { helper(); std::thread t(&Engine::loop, this); }
}
"""


def test_strip_and_preprocess() -> None:
    text = strip_comments_and_literals('a(); /* b(); */ c("d();"); // e();\nf();')
    assert "b()" not in text and "d()" not in text and "e()" not in text
    assert "a();" in text and "f();" in text
    pre = blank_preprocessor("#if 0\nx();\n#else\ny();\n#endif\n")
    assert "x();" not in pre and "y();" in pre


def test_regex_function_definitions(settings: Settings) -> None:
    (analysis,) = analyze_regex(settings, [SourceFile("src/app/run.c", C_SOURCE)])
    funcs = {f.name: f for f in analysis.functions}
    assert set(funcs) == {"helper", "live_code", "run"}
    assert funcs["helper"].is_static and not funcs["run"].is_static
    assert funcs["run"].start_line == 13
    assert funcs["run"].end_line == 24


def test_regex_calls_pointers_async_refs(settings: Settings) -> None:
    (analysis,) = analyze_regex(settings, [SourceFile("src/app/run.c", C_SOURCE)])
    run_idx = [f.name for f in analysis.functions].index("run")
    calls = {(c.name, c.kind) for c in analysis.calls if c.caller == run_idx}
    assert ("helper", CALL_DIRECT) in calls
    assert ("on_done", CALL_FIELD) in calls
    assert ("cb", CALL_POINTER) in calls  # 引数の関数ポインタ
    assert ("fp", CALL_FIELD) in calls  # (*ctx->fp)(...)
    assert not any(name in ("fake_call", "string_call") for name, _ in calls)
    asyncs = {(a.api, a.kind, a.key, a.target, a.handle) for a in analysis.async_calls}
    assert ("OsMsgQSend", "queue_send", "requestQueue", None, None) in asyncs
    assert ("OsTaskCreate", "thread", None, "worker_main", "s_task") in asyncs
    refs = {(r.name, r.field_name, r.via_call) for r in analysis.refs}
    assert ("on_done_cb", "on_done", None) in refs  # 指示付き初期化子
    assert ("on_event", None, "register_callback") in refs


def test_regex_cpp_methods(settings: Settings) -> None:
    (analysis,) = analyze_regex(settings, [SourceFile("src/app/engine.cpp", CPP_SOURCE)])
    names = [f.name for f in analysis.functions]
    assert names == ["Engine::start", "Engine::run"]
    threads = [a for a in analysis.async_calls if a.kind == "thread"]
    assert threads and threads[0].target == "Engine::loop"


@pytest.mark.skipif(not libclang_available(), reason="libclang が利用できない")
def test_clang_analyzer_on_sample(tmp_settings: Settings, sample_dump: Path) -> None:
    settings = dataclasses.replace(
        tmp_settings, analyzer=dataclasses.replace(tmp_settings.analyzer, jobs=2)
    )
    repo = SourceDumpReader(sample_dump, settings.source)
    backend, raws = CachedSourceAnalysis(settings, repo).load("clang", use_cache=True)
    assert backend == "clang"
    by_path = {r.path: r for r in raws}
    tm = by_path["src/job/task_manager.c"]
    names = {f.name for f in tm.functions}
    assert {"create_task", "execute_task", "process_job", "task_worker"} <= names
    process = [f.name for f in tm.functions].index("process_job")
    kinds = {(c.name, c.kind) for c in tm.calls if c.caller == process}
    assert ("on_done", CALL_FIELD) in kinds
    assert ("handler", CALL_POINTER) in kinds
    # 2 回目はキャッシュから読む
    _, cached = CachedSourceAnalysis(settings, repo).load("clang", use_cache=True)
    assert len(cached) == len(raws)


def test_resolve_backend() -> None:
    assert resolve_backend("regex") == "regex"
    assert resolve_backend("auto") in ("clang", "regex")
    with pytest.raises(ValueError):
        resolve_backend("unknown")
