"""LogEntry → ソース上の関数（function_id）の対応付け。"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from ..domain.models import CallGraph, FunctionInfo, LogEntry


class LogFunctionResolver:
    """(ファイル名, 関数名, 行番号) から関数を特定する。

    1. ファイル名＋関数名で検索
    2. 見つからなければ関数名のみ（TRACE 形式などファイル名がないログ）
    3. 候補が複数ならログの行番号を含む定義範囲で絞り込む
    """

    def __init__(self, graph: CallGraph) -> None:
        self._by_file_func: dict[tuple[str, str], list[FunctionInfo]] = defaultdict(list)
        self._by_func: dict[str, list[FunctionInfo]] = defaultdict(list)
        for info in graph.functions.values():
            short = info.name.rsplit("::", 1)[-1]
            base = info.basename.lower()
            self._by_file_func[(base, info.name)].append(info)
            self._by_func[info.name].append(info)
            if short != info.name:
                self._by_file_func[(base, short)].append(info)
                self._by_func[short].append(info)
        self._cache: dict[tuple[Optional[str], str, Optional[int]], Optional[FunctionInfo]] = {}

    def resolve(self, entry: LogEntry) -> Optional[FunctionInfo]:
        if not entry.function_name:
            return None
        key = (entry.file_name, entry.function_name, entry.line_number)
        if key in self._cache:
            return self._cache[key]
        result = self._lookup(entry.file_name, entry.function_name, entry.line_number)
        self._cache[key] = result
        return result

    def _lookup(
        self, file_name: Optional[str], function: str, line: Optional[int]
    ) -> Optional[FunctionInfo]:
        candidates: list[FunctionInfo] = []
        if file_name:
            base = file_name.replace("\\", "/").rsplit("/", 1)[-1].lower()
            candidates = self._by_file_func.get((base, function), [])
        if not candidates:
            candidates = self._by_func.get(function, [])
        if not candidates:
            return None
        if len(candidates) > 1 and line is not None:
            inside = [c for c in candidates if c.contains_line(line)]
            if inside:
                candidates = inside
        return candidates[0]
