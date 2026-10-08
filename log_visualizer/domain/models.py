"""ドメインモデル（エンティティ・値オブジェクト）。

このモジュールは標準ライブラリ以外に依存しない。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterable, Iterator, Optional

UNRESOLVED_FUNCTION_POINTER = "UNRESOLVED_FUNCTION_POINTER"


class LogLevel(str, Enum):
    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, text: Optional[str]) -> "LogLevel":
        if not text:
            return cls.UNKNOWN
        key = text.strip().upper()
        if key == "WARNING":
            key = "WARN"
        try:
            return cls(key)
        except ValueError:
            return cls.UNKNOWN


class LogKind(str, Enum):
    """ログレコードの書式種別。"""

    STRUCTURED = "STRUCTURED"  # ut_log 形式（モジュール欄あり）
    TRACE = "TRACE"  # *****...[func(line)] 形式
    RAW = "RAW"  # 上記以外（バナー・panic など）


class CallKind(str, Enum):
    """呼び出し種別。描画時の線種に対応する。"""

    SYNC = "SYNC"  # 同期呼び出し: 実線
    ASYNC = "ASYNC"  # 非同期通知: 点線
    CALLBACK = "CALLBACK"  # コールバック: 破線

    @staticmethod
    def combine(kinds: Iterable["CallKind"]) -> "CallKind":
        """経路上の辺種別から経路全体の種別を決める（CALLBACK > ASYNC > SYNC）。"""
        result = CallKind.SYNC
        for kind in kinds:
            if kind is CallKind.CALLBACK:
                return CallKind.CALLBACK
            if kind is CallKind.ASYNC:
                result = CallKind.ASYNC
        return result


@dataclass(frozen=True, slots=True)
class LogEntry:
    """ログ 1 レコード（複数物理行から組み立てられることがある）。"""

    seq: int
    line_no: int
    timestamp: Optional[datetime]
    timestamp_ms: Optional[int]
    thread_id: Optional[str]
    tick: Optional[int]
    log_level: LogLevel
    module_name: Optional[str]
    function_name: Optional[str]
    message: str
    file_name: Optional[str]
    line_number: Optional[int]
    kind: LogKind = LogKind.STRUCTURED
    raw: str = ""


@dataclass(frozen=True, slots=True)
class SourceFile:
    """ソースダンプ内の 1 ファイル。"""

    path: str  # "/" 区切りの正規化パス
    content: str

    @property
    def basename(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True, slots=True)
class FunctionInfo:
    """関数定義。"""

    function_id: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    is_static: bool
    module: str

    @property
    def basename(self) -> str:
        return self.file_path.rsplit("/", 1)[-1]

    def contains_line(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line


@dataclass(frozen=True, slots=True)
class CallEdge:
    """呼び出し関係 1 本。"""

    caller: str
    callee: str
    kind: CallKind
    line: int
    via: Optional[str] = None


@dataclass
class CallGraph:
    """関数呼び出しグラフ（順方向・逆方向の隣接リストを保持）。"""

    functions: dict[str, FunctionInfo] = field(default_factory=dict)
    _out: dict[str, dict[str, CallEdge]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    _in: dict[str, dict[str, CallEdge]] = field(
        default_factory=lambda: defaultdict(dict)
    )

    def add_function(self, info: FunctionInfo) -> None:
        self.functions[info.function_id] = info

    def add_edge(self, edge: CallEdge) -> None:
        """辺を追加する。同じ (caller, callee) は種別の強い方を残す。"""
        existing = self._out[edge.caller].get(edge.callee)
        if existing is not None:
            if CallKind.combine([existing.kind, edge.kind]) is existing.kind:
                return
        self._out[edge.caller][edge.callee] = edge
        self._in[edge.callee][edge.caller] = edge

    def callees(self, function_id: str) -> list[CallEdge]:
        return list(self._out.get(function_id, {}).values())

    def callers(self, function_id: str) -> list[CallEdge]:
        return list(self._in.get(function_id, {}).values())

    def edges(self) -> Iterator[CallEdge]:
        for targets in self._out.values():
            yield from targets.values()

    def edge_count(self) -> int:
        return sum(len(t) for t in self._out.values())

    def to_adjacency(self) -> dict[str, list[str]]:
        """要件形式の隣接リスト {caller: [callee, ...]}。"""
        return {
            caller: sorted(targets)
            for caller, targets in sorted(self._out.items())
            if targets
        }


@dataclass(frozen=True, slots=True)
class SequenceEvent:
    """シーケンス図上の 1 イベント（ログ 1 件＋解析結果）。"""

    entry: LogEntry
    lane: str
    function_id: Optional[str]
    parent_seq: Optional[int]
    arrow_kind: Optional[CallKind]
    depth: int
    hops: int = 0
