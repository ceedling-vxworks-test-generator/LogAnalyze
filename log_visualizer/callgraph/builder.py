"""RawFileAnalysis 群から CallGraph を構築する。"""

from __future__ import annotations

import fnmatch
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..analyzer.module_resolver import ModuleResolver
from ..analyzer.raw_model import CALL_DIRECT, CALL_FIELD, RawFileAnalysis
from ..domain.models import (
    UNRESOLVED_FUNCTION_POINTER,
    CallEdge,
    CallGraph,
    CallKind,
    FunctionInfo,
)
from .function_id import make_function_id, unique_suffixes

LOG = logging.getLogger(__name__)

HEADER_EXTENSIONS = (".h", ".hh", ".hpp", ".hxx", ".inl")
CXX_EXTENSIONS = (".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx")
# フィールド名で解決する関数ポインタ候補の上限（超えたら解決不能扱い）
MAX_FIELD_TARGETS = 12


@dataclass
class BuildStats:
    files: int = 0
    functions: int = 0
    edges: int = 0
    unresolved_pointer_calls: int = 0
    async_edges: int = 0
    callback_edges: int = 0
    files_with_errors: int = 0
    errors: list[str] = field(default_factory=list)


def _common_prefix_len(a: str, b: str) -> int:
    return len(os.path.commonprefix([a.split("/"), b.split("/")]))


class CallGraphBuilder:
    def __init__(
        self, module_resolver: ModuleResolver, exclude_callees: Iterable[str] = ()
    ) -> None:
        self._modules = module_resolver
        self._exclude = tuple(exclude_callees)
        self.stats = BuildStats()

    def _excluded(self, name: str) -> bool:
        short = name.rsplit("::", 1)[-1]
        return any(
            fnmatch.fnmatchcase(name, p) or fnmatch.fnmatchcase(short, p) for p in self._exclude
        )

    def build(self, analyses: Iterable[RawFileAnalysis]) -> CallGraph:
        items = list(analyses)
        graph = CallGraph()
        self.stats = BuildStats(files=len(items))

        suffixes = unique_suffixes(a.path for a in items if a.functions)
        # (path, 添字) → function_id
        local_ids: dict[tuple[str, int], str] = {}
        by_name: dict[str, list[FunctionInfo]] = defaultdict(list)
        by_short: dict[str, list[FunctionInfo]] = defaultdict(list)

        for analysis in items:
            if analysis.errors:
                self.stats.files_with_errors += 1
                if len(self.stats.errors) < 20:
                    self.stats.errors.append(f"{analysis.path}: {analysis.errors[0]}")
            module = self._modules.module_for_path(analysis.path)
            for index, raw in enumerate(analysis.functions):
                fid = make_function_id(suffixes[analysis.path], raw.name)
                local_ids[(analysis.path, index)] = fid
                if fid in graph.functions:
                    continue
                info = FunctionInfo(
                    function_id=fid,
                    name=raw.name,
                    file_path=analysis.path,
                    start_line=raw.start_line,
                    end_line=raw.end_line,
                    is_static=raw.is_static,
                    module=module,
                )
                graph.add_function(info)
                by_name[raw.name].append(info)
                if "::" in raw.name:
                    by_short[raw.name.rsplit("::", 1)[-1]].append(info)

        resolver = _NameResolver(by_name, by_short)
        excluded = {fid for fid, f in graph.functions.items() if self._excluded(f.name)}
        add_edge = graph.add_edge

        def add(edge: CallEdge) -> None:
            if edge.callee not in excluded:
                add_edge(edge)

        def caller_info(path: str, index: int) -> Optional[FunctionInfo]:
            fid = local_ids.get((path, index))
            return graph.functions.get(fid) if fid else None

        field_map: dict[str, set[str]] = defaultdict(set)
        handle_map: dict[str, set[str]] = defaultdict(set)
        queue_senders: dict[str, list[tuple[FunctionInfo, int, str]]] = defaultdict(list)
        queue_receivers: dict[str, list[FunctionInfo]] = defaultdict(list)
        event_sends: list[tuple[FunctionInfo, str, int, str]] = []

        # 1st pass: 関数参照（コールバック登録）・非同期 API
        for analysis in items:
            for ref in analysis.refs:
                ctx = caller_info(analysis.path, ref.caller) if ref.caller is not None else None
                target = resolver.resolve(ref.name, ctx, analysis.path)
                if target is None:
                    continue
                if ref.field_name:
                    field_map[ref.field_name].add(target.function_id)
                if ctx is not None and ctx.function_id != target.function_id:
                    via = ref.via_call or (f"field:{ref.field_name}" if ref.field_name else "assign")
                    add(
                        CallEdge(ctx.function_id, target.function_id, CallKind.CALLBACK,
                                 ref.line, f"register:{via}")
                    )
            for acall in analysis.async_calls:
                ctx = caller_info(analysis.path, acall.caller)
                if ctx is None:
                    continue
                if acall.kind in ("thread", "callback_register") and acall.target:
                    target = resolver.resolve(acall.target, ctx, analysis.path)
                    if target is None:
                        continue
                    kind = CallKind.ASYNC if acall.kind == "thread" else CallKind.CALLBACK
                    add(
                        CallEdge(ctx.function_id, target.function_id, kind, acall.line, acall.api)
                    )
                    if acall.handle:
                        handle_map[acall.handle].add(target.function_id)
                elif acall.kind == "event_send" and acall.key:
                    event_sends.append((ctx, acall.key, acall.line, acall.api))
                elif acall.kind == "queue_send" and acall.key:
                    queue_senders[acall.key].append((ctx, acall.line, acall.api))
                elif acall.kind == "queue_receive" and acall.key:
                    queue_receivers[acall.key].append(ctx)

        # 2nd pass: 呼び出し
        for analysis in items:
            is_cxx = analysis.path.lower().endswith(CXX_EXTENSIONS)
            for call in analysis.calls:
                ctx = caller_info(analysis.path, call.caller)
                if ctx is None:
                    continue
                if call.kind == CALL_DIRECT:
                    target = resolver.resolve(call.name, ctx, analysis.path)
                    if target is not None:
                        add(
                            CallEdge(ctx.function_id, target.function_id, CallKind.SYNC, call.line)
                        )
                    continue
                if call.kind == CALL_FIELD and is_cxx:
                    target = resolver.resolve_method(call.name, ctx)
                    if target is not None:
                        add(
                            CallEdge(ctx.function_id, target.function_id, CallKind.SYNC, call.line)
                        )
                        continue
                    if call.name not in field_map:
                        continue  # 標準ライブラリ等のメソッド
                targets = field_map.get(call.name, set()) if call.kind == CALL_FIELD else set()
                if targets and len(targets) <= MAX_FIELD_TARGETS:
                    for fid in sorted(targets):
                        if fid != ctx.function_id:
                            add(
                                CallEdge(ctx.function_id, fid, CallKind.CALLBACK, call.line,
                                         f"field:{call.name}")
                            )
                    continue
                self.stats.unresolved_pointer_calls += 1
                add(
                    CallEdge(ctx.function_id, UNRESOLVED_FUNCTION_POINTER, CallKind.SYNC,
                             call.line, f"{call.kind}:{call.name}")
                )

        # 非同期: イベント送信 → 送信先タスクのエントリ関数
        for ctx, key, line, api in event_sends:
            for fid in sorted(handle_map.get(key, ())):
                if fid != ctx.function_id:
                    add(CallEdge(ctx.function_id, fid, CallKind.ASYNC, line, f"{api}:{key}"))

        # 非同期: キュー送信 → 同じキーの受信関数（パスが近いものを優先）
        for key, senders in queue_senders.items():
            receivers = queue_receivers.get(key, [])
            if not receivers:
                continue
            for sender, line, api in senders:
                scored = [(_common_prefix_len(sender.file_path, r.file_path), r) for r in receivers]
                best = max(score for score, _ in scored)
                for score, receiver in scored:
                    if score == best and receiver.function_id != sender.function_id:
                        add(
                            CallEdge(sender.function_id, receiver.function_id, CallKind.ASYNC,
                                     line, f"{api}:{key}")
                        )

        self.stats.functions = len(graph.functions)
        self.stats.edges = graph.edge_count()
        for edge in graph.edges():
            if edge.kind is CallKind.ASYNC:
                self.stats.async_edges += 1
            elif edge.kind is CallKind.CALLBACK:
                self.stats.callback_edges += 1
        return graph


class _NameResolver:
    """呼び出し名 → FunctionInfo。"""

    def __init__(
        self,
        by_name: dict[str, list[FunctionInfo]],
        by_short: dict[str, list[FunctionInfo]],
    ) -> None:
        self._by_name = by_name
        self._by_short = by_short

    def resolve(
        self, name: str, ctx: Optional[FunctionInfo], path: str
    ) -> Optional[FunctionInfo]:
        name = name.lstrip(":")
        candidates = self._by_name.get(name)
        if not candidates and ctx is not None and "::" in ctx.name and "::" not in name:
            # クラスメソッド内からの非修飾呼び出し
            cls = ctx.name.rsplit("::", 1)[0]
            candidates = self._by_name.get(f"{cls}::{name}")
        if not candidates and "::" in name:
            candidates = self._by_short.get(name.rsplit("::", 1)[-1])
        if not candidates:
            return None
        same_file = [c for c in candidates if c.file_path == path]
        if same_file:
            return same_file[0]
        visible = [
            c for c in candidates
            if not c.is_static or c.file_path.lower().endswith(HEADER_EXTENSIONS)
        ]
        if not visible:
            return None
        if len(visible) == 1:
            return visible[0]
        return max(visible, key=lambda c: _common_prefix_len(c.file_path, path))

    def resolve_method(self, name: str, ctx: FunctionInfo) -> Optional[FunctionInfo]:
        candidates = self._by_short.get(name)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        return max(candidates, key=lambda c: _common_prefix_len(c.file_path, ctx.file_path))
