"""ユースケース: ログ＋ソース → CallGraph 出力＋シーケンス図 HTML。

具象クラスには依存せず、ポート（Protocol）経由で各部品を受け取る。
部品の組み立ては cli.py が行う。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional, Protocol

from ..domain.models import CallGraph, LogEntry, SequenceEvent
from ..domain.ports import (
    CallGraphProvider,
    CallGraphWriter,
    LogReader,
    SequenceGenerator,
    SourceRepository,
)

LOG = logging.getLogger(__name__)


class HtmlSequenceRenderer(Protocol):
    def render(
        self,
        events: Iterable[SequenceEvent],
        graph: CallGraph,
        sources: SourceRepository,
        out_path: Path,
        title: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> Path:
        ...


@dataclass(frozen=True)
class AnalyzeRequest:
    log_path: Path
    out_dir: Path
    html_name: str = "sequence.html"
    title: Optional[str] = None
    time_from: Optional[datetime] = None
    time_to: Optional[datetime] = None
    modules: frozenset[str] = frozenset()
    max_entries: Optional[int] = None


@dataclass
class AnalyzeResult:
    html_path: Path
    callgraph_files: list[Path]
    event_count: int
    elapsed_sec: float
    extra: dict[str, Any] = field(default_factory=dict)


class AnalyzeUseCase:
    def __init__(
        self,
        log_reader: LogReader,
        sources: SourceRepository,
        callgraph_provider: CallGraphProvider,
        sequence_factory: Callable[[CallGraph], SequenceGenerator],
        renderer: HtmlSequenceRenderer,
        callgraph_writer: CallGraphWriter,
    ) -> None:
        self._log_reader = log_reader
        self._sources = sources
        self._callgraph_provider = callgraph_provider
        self._sequence_factory = sequence_factory
        self._renderer = renderer
        self._writer = callgraph_writer

    def execute(self, request: AnalyzeRequest, meta: Optional[dict[str, Any]] = None) -> AnalyzeResult:
        started = time.perf_counter()
        LOG.info("[1/3] ソース解析・CallGraph 構築")
        graph = self._callgraph_provider.build_callgraph()
        cg_files = self._writer.write(graph, request.out_dir)
        for path in cg_files:
            LOG.info("  出力: %s", path)

        LOG.info("[2/3] ログ解析・シーケンス生成（ストリーミング）")
        entries = self._filter_entries(self._log_reader.parse(request.log_path), request)
        events = self._sequence_factory(graph).build(entries)
        if request.modules:
            events = (e for e in events if e.lane in request.modules)

        LOG.info("[3/3] HTML 出力")
        full_meta = dict(meta or {})
        full_meta.update(self._callgraph_provider.describe())
        counter = _Counter(events)
        html_path = self._renderer.render(
            counter,
            graph,
            self._sources,
            request.out_dir / request.html_name,
            title=request.title or f"Log Sequence - {request.log_path.name}",
            meta=full_meta,
        )
        elapsed = time.perf_counter() - started
        LOG.info("完了: %s（%d イベント, %.1f 秒）", html_path, counter.count, elapsed)
        return AnalyzeResult(html_path, cg_files, counter.count, elapsed)

    @staticmethod
    def _filter_entries(
        entries: Iterable[LogEntry], request: AnalyzeRequest
    ) -> Iterator[LogEntry]:
        count = 0
        for entry in entries:
            ts = entry.timestamp
            if ts is not None:
                if request.time_from is not None and ts < request.time_from:
                    continue
                if request.time_to is not None and ts > request.time_to:
                    continue
            yield entry
            count += 1
            if request.max_entries is not None and count >= request.max_entries:
                break


class _Counter:
    """イテレータを通過した件数を数える。"""

    def __init__(self, events: Iterable[SequenceEvent]) -> None:
        self._events = events
        self.count = 0

    def __iter__(self) -> Iterator[SequenceEvent]:
        for event in self._events:
            self.count += 1
            yield event
