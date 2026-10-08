"""ソースから CallGraph を作る（解析器選択・キャッシュ・構築をまとめる）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..analyzer.analyzer_factory import CachedSourceAnalysis
from ..analyzer.module_resolver import ModuleResolver
from ..config.settings import Settings
from ..domain.models import CallGraph
from ..domain.ports import SourceRepository
from .builder import BuildStats, CallGraphBuilder

LOG = logging.getLogger(__name__)


@dataclass
class CallGraphResult:
    graph: CallGraph
    backend: str
    stats: BuildStats = field(default_factory=BuildStats)


class SourceCallGraphProvider:
    def __init__(
        self,
        settings: Settings,
        repository: SourceRepository,
        backend: Optional[str] = None,
        use_cache: bool = True,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._backend = backend
        self._use_cache = use_cache
        self.result: Optional[CallGraphResult] = None

    def build_callgraph(self) -> CallGraph:
        backend, raws = CachedSourceAnalysis(self._settings, self._repository).load(
            self._backend, self._use_cache
        )
        builder = CallGraphBuilder(
            ModuleResolver(self._settings.modules), self._settings.analyzer.exclude_callees
        )
        graph = builder.build(raws)
        s = builder.stats
        LOG.info(
            "CallGraph: 関数 %d / 辺 %d（非同期 %d, コールバック %d, 未解決ポインタ %d）",
            s.functions, s.edges, s.async_edges, s.callback_edges, s.unresolved_pointer_calls,
        )
        if s.files_with_errors:
            LOG.info("解析で致命的エラーのあったファイル: %d（例: %s）", s.files_with_errors,
                     s.errors[0] if s.errors else "-")
        self.result = CallGraphResult(graph, backend, s)
        return graph

    def describe(self) -> dict[str, Any]:
        if self.result is None:
            return {}
        s = self.result.stats
        return {
            "backend": self.result.backend,
            "functions": s.functions,
            "edges": s.edges,
            "unresolved_pointer_calls": s.unresolved_pointer_calls,
        }


class EmptyCallGraphProvider:
    """ソースなし（ログのみ）で実行する場合。"""

    def __init__(self) -> None:
        self.result = CallGraphResult(CallGraph(), "none")

    def build_callgraph(self) -> CallGraph:
        return self.result.graph

    def describe(self) -> dict[str, Any]:
        return {"backend": "none"}
