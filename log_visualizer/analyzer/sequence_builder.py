"""ログ列 + CallGraph → シーケンスイベント（矢印・呼び出し深度）。

ストリーミング処理。保持するのは「関数ごとの直近ログ」だけで、
メモリ使用量はログ件数ではなく関数数に比例する。
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

from ..config.settings import SequenceSettings
from ..domain.models import (
    UNRESOLVED_FUNCTION_POINTER,
    CallGraph,
    CallKind,
    LogEntry,
    SequenceEvent,
)
from .function_resolver import LogFunctionResolver
from .module_resolver import ModuleResolver


@dataclass(frozen=True, slots=True)
class _Recent:
    seq: int
    ts: Optional[int]
    depth: int
    parent: Optional[int]


@dataclass(frozen=True, slots=True)
class CallerPath:
    hops: int
    kind: CallKind


class CallerReach:
    """CallGraph を逆向きに辿り、max_hops 以内の呼び出し元を求める（LRU キャッシュ付き）。"""

    def __init__(self, graph: CallGraph, max_hops: int, cache_size: int = 4096) -> None:
        self._graph = graph
        self._max_hops = max_hops
        self._cache: OrderedDict[str, dict[str, CallerPath]] = OrderedDict()
        self._cache_size = cache_size

    def callers_of(self, function_id: str) -> dict[str, CallerPath]:
        cached = self._cache.get(function_id)
        if cached is not None:
            self._cache.move_to_end(function_id)
            return cached
        result: dict[str, CallerPath] = {}
        queue: deque[tuple[str, int, CallKind]] = deque([(function_id, 0, CallKind.SYNC)])
        seen = {function_id}
        while queue:
            current, hops, kind = queue.popleft()
            if hops >= self._max_hops:
                continue
            for edge in self._graph.callers(current):
                caller = edge.caller
                if caller in seen or caller == UNRESOLVED_FUNCTION_POINTER:
                    continue
                seen.add(caller)
                path_kind = CallKind.combine([kind, edge.kind])
                result[caller] = CallerPath(hops + 1, path_kind)
                queue.append((caller, hops + 1, path_kind))
        self._cache[function_id] = result
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return result


class SequenceBuilder:
    def __init__(
        self,
        graph: CallGraph,
        modules: ModuleResolver,
        settings: SequenceSettings,
        resolver: Optional[LogFunctionResolver] = None,
    ) -> None:
        self._graph = graph
        self._modules = modules
        self._settings = settings
        self._resolver = resolver or LogFunctionResolver(graph)
        self._reach = CallerReach(graph, settings.max_caller_hops)
        self._windows = {
            CallKind.SYNC: settings.sync_window_ms,
            CallKind.ASYNC: settings.async_window_ms,
            CallKind.CALLBACK: settings.callback_window_ms,
        }

    def build(self, entries: Iterable[LogEntry]) -> Iterator[SequenceEvent]:
        recent: dict[str, _Recent] = {}
        for entry in entries:
            info = self._resolver.resolve(entry)
            lane = self._modules.lane_for(entry, info)
            fid = info.function_id if info is not None else None
            key = fid or self._fallback_key(entry)

            parent: Optional[int] = None
            arrow: Optional[CallKind] = None
            depth = 0
            hops = 0

            if fid is not None:
                best = self._find_caller(fid, entry.timestamp_ms, recent)
                if best is not None:
                    caller_recent, path = best
                    parent = caller_recent.seq
                    arrow = path.kind
                    depth = caller_recent.depth + 1
                    hops = path.hops
            if parent is None and key is not None:
                prev = recent.get(key)
                if prev is not None and self._within(prev.ts, entry.timestamp_ms, CallKind.SYNC):
                    # 同じ関数の連続ログ: 兄弟として扱う
                    parent = prev.parent
                    depth = prev.depth

            if key is not None:
                recent[key] = _Recent(entry.seq, entry.timestamp_ms, depth, parent)

            yield SequenceEvent(
                entry=entry,
                lane=lane,
                function_id=fid,
                parent_seq=parent,
                arrow_kind=arrow,
                depth=depth,
                hops=hops,
            )

    def _find_caller(
        self, fid: str, ts: Optional[int], recent: dict[str, _Recent]
    ) -> Optional[tuple[_Recent, CallerPath]]:
        best: Optional[tuple[_Recent, CallerPath]] = None
        for caller, path in self._reach.callers_of(fid).items():
            candidate = recent.get(caller)
            if candidate is None or not self._within(candidate.ts, ts, path.kind):
                continue
            if best is None:
                best = (candidate, path)
                continue
            # 最も新しいログを優先。同時刻ならホップ数が少ない方
            if (candidate.seq, -path.hops) > (best[0].seq, -best[1].hops):
                best = (candidate, path)
        return best

    def _within(self, earlier: Optional[int], later: Optional[int], kind: CallKind) -> bool:
        if earlier is None or later is None:
            return True
        delta = later - earlier
        return 0 <= delta <= self._windows[kind]

    @staticmethod
    def _fallback_key(entry: LogEntry) -> Optional[str]:
        if not entry.function_name:
            return None
        return f"?{entry.file_name or ''}::{entry.function_name}"
