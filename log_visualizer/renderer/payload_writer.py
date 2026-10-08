"""HTML に埋め込むデータ（payload）の逐次書き出し。

payload は 1 つの JSON オブジェクトを gzip 圧縮したもの:

    {
      "events": [[...], ...],          # 1 イベント = 1 配列（列定義は EVENT_COLUMNS）
      "lanes": [...], "strings": {...}, "graph": {...}, "sources": {...}, "meta": {...}
    }

events を先頭に置き、イベントが流れてくるたびに gzip ストリームへ書く。
文字列はインターン表で整数化する。
"""

from __future__ import annotations

import gzip
import io
import json
import os
import tempfile
from pathlib import Path
from typing import IO, Any, Iterable, Optional

from ..domain.models import (
    UNRESOLVED_FUNCTION_POINTER,
    CallGraph,
    CallKind,
    LogKind,
    LogLevel,
    SequenceEvent,
    SourceFile,
)

EVENT_COLUMNS = [
    "seq",  # 0
    "line_no",  # 1 ログファイル上の行
    "ts",  # 2 base_ts からの相対ミリ秒（null 可）
    "level",  # 3 LEVELS の添字
    "kind",  # 4 KINDS の添字
    "lane",  # 5 lanes の添字
    "func",  # 6 strings.func の添字（-1 = なし）
    "file",  # 7 strings.file の添字（-1 = なし）
    "line",  # 8 ソース行番号（-1 = なし）
    "fid",  # 9 graph.nodes の添字（-1 = 未解決）
    "parent",  # 10 親イベントの seq（-1 = なし）
    "arrow",  # 11 ARROWS の添字
    "depth",  # 12
    "hops",  # 13
    "msg",  # 14
    "module",  # 15 strings.module（ログのモジュール欄の生値, -1 = なし）
    "thread",  # 16 strings.thread（-1 = なし）
    "tick",  # 17 [N] の値（null 可）
    "raw",  # 18 生テキスト（再構成できない場合のみ。それ以外は ""）
]
LEVELS = [level.value for level in LogLevel]
KINDS = [kind.value for kind in LogKind]
ARROWS = ["", CallKind.SYNC.value, CallKind.ASYNC.value, CallKind.CALLBACK.value]


class _Interner:
    def __init__(self) -> None:
        self.values: list[str] = []
        self._index: dict[str, int] = {}

    def get(self, value: Optional[str]) -> int:
        if value is None:
            return -1
        idx = self._index.get(value)
        if idx is None:
            idx = len(self.values)
            self._index[value] = idx
            self.values.append(value)
        return idx


class PayloadWriter:
    def __init__(
        self,
        graph: CallGraph,
        work_dir: Path,
        max_message_length: int = 4000,
    ) -> None:
        self._graph = graph
        self._max_msg = max_message_length
        self._node_ids = sorted(graph.functions) + [UNRESOLVED_FUNCTION_POINTER]
        self._node_index = {fid: i for i, fid in enumerate(self._node_ids)}
        self.lanes = _Interner()
        self._func = _Interner()
        self._file = _Interner()
        self._module = _Interner()
        self._thread = _Interner()
        self.base_ts: Optional[int] = None
        self.last_ts: Optional[int] = None
        self.count = 0
        self.referenced_fids: set[str] = set()
        self.referenced_files: set[str] = set()
        work_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="lv_payload_", suffix=".json.gz", dir=work_dir)
        os.close(fd)
        self.path = Path(name)
        self._raw = gzip.GzipFile(self.path, "wb", compresslevel=6)
        self._out = io.TextIOWrapper(self._raw, encoding="utf-8")
        self._out.write('{"events":[')

    def add(self, event: SequenceEvent) -> None:
        e = event.entry
        ts = e.timestamp_ms
        if ts is not None:
            if self.base_ts is None:
                self.base_ts = ts
            self.last_ts = ts
        rel_ts = ts - self.base_ts if ts is not None and self.base_ts is not None else None
        if event.function_id:
            self.referenced_fids.add(event.function_id)
        if e.file_name:
            self.referenced_files.add(e.file_name)
        msg = e.message
        if len(msg) > self._max_msg:
            msg = msg[: self._max_msg] + " …(truncated)"
        raw = ""
        if e.kind is not LogKind.STRUCTURED or "\n" in e.raw:
            raw = e.raw if len(e.raw) <= self._max_msg else e.raw[: self._max_msg]
        row = [
            e.seq,
            e.line_no,
            rel_ts,
            LEVELS.index(e.log_level.value),
            KINDS.index(e.kind.value),
            self.lanes.get(event.lane),
            self._func.get(e.function_name),
            self._file.get(e.file_name),
            e.line_number if e.line_number is not None else -1,
            self._node_index.get(event.function_id, -1) if event.function_id else -1,
            event.parent_seq if event.parent_seq is not None else -1,
            ARROWS.index(event.arrow_kind.value) if event.arrow_kind else 0,
            event.depth,
            event.hops,
            msg,
            self._module.get(e.module_name),
            self._thread.get(e.thread_id),
            e.tick,
        ]
        row.append(raw)
        if self.count:
            self._out.write(",")
        self._out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        self.count += 1

    def finish(self, meta: dict[str, Any], sources: Iterable[SourceFile]) -> Path:
        out = self._out
        out.write("],")
        out.write('"columns":' + json.dumps(EVENT_COLUMNS) + ",")
        out.write('"levels":' + json.dumps(LEVELS) + ",")
        out.write('"kinds":' + json.dumps(KINDS) + ",")
        out.write('"arrows":' + json.dumps(ARROWS) + ",")
        out.write('"lanes":' + json.dumps(self.lanes.values, ensure_ascii=False) + ",")
        strings = {
            "func": self._func.values,
            "file": self._file.values,
            "module": self._module.values,
            "thread": self._thread.values,
        }
        out.write('"strings":' + json.dumps(strings, ensure_ascii=False) + ",")
        out.write('"graph":')
        self._write_graph(out)
        out.write(',"sources":{')
        first = True
        for source in sources:
            if not first:
                out.write(",")
            first = False
            out.write(json.dumps(source.path, ensure_ascii=False))
            out.write(":")
            out.write(json.dumps(source.content, ensure_ascii=False))
        out.write("},")
        meta = dict(meta)
        meta.update(
            {
                "event_count": self.count,
                "base_ts": self.base_ts,
                "last_ts": self.last_ts,
                "has_thread": bool(self._thread.values),
            }
        )
        out.write('"meta":' + json.dumps(meta, ensure_ascii=False, default=str))
        out.write("}")
        out.flush()
        out.close()
        return self.path

    def _write_graph(self, out: IO[str]) -> None:
        graph = self._graph
        modules = _Interner()
        files = _Interner()
        info_rows: list[list[Any]] = []
        for fid in self._node_ids:
            info = graph.functions.get(fid)
            if info is None:
                info_rows.append([fid, -1, 0, 0, -1, 0])
            else:
                info_rows.append(
                    [
                        info.name,
                        files.get(info.file_path),
                        info.start_line,
                        info.end_line,
                        modules.get(info.module),
                        1 if info.is_static else 0,
                    ]
                )
        vias = _Interner()
        edges = [
            [
                self._node_index[e.caller],
                self._node_index[e.callee],
                ARROWS.index(e.kind.value),
                e.line,
                vias.get(e.via),
            ]
            for e in graph.edges()
            if e.caller in self._node_index and e.callee in self._node_index
        ]
        data = {
            "nodes": self._node_ids,
            "info": info_rows,
            "files": files.values,
            "modules": modules.values,
            "vias": vias.values,
            "edges": edges,
        }
        out.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    def discard(self) -> None:
        try:
            if not self._out.closed:
                self._out.close()
        finally:
            self.path.unlink(missing_ok=True)
