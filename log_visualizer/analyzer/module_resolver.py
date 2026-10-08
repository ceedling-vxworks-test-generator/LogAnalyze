"""モジュール（シーケンス図の横軸）の決定。

優先順位:
  1. ログのモジュール欄（"PCL:xxx" → "PCL"。別名ルールを先に適用）
  2. ファイル名ルール
  3. ディレクトリルール
"""

from __future__ import annotations

import fnmatch
import re
from typing import Optional

from ..config.settings import ModuleSettings
from ..domain.models import FunctionInfo, LogEntry, LogKind


class ModuleResolver:
    def __init__(self, settings: ModuleSettings) -> None:
        self._settings = settings
        self._aliases = [(re.compile(a.pattern), a.module) for a in settings.aliases]
        self._file_rules = [(r.pattern.lower(), r.module) for r in settings.file_rules]
        self._dir_rules = [re.compile(p) for p in settings.directory_rules]
        self._log_cache: dict[str, str] = {}
        # ログのモジュール欄から得たレーン名（小文字 → 表記）。
        # ディレクトリ由来の名前が大文字小文字違いで一致する場合はこちらに寄せる。
        self._log_lanes: dict[str, str] = {}

    @property
    def raw_lane(self) -> str:
        return self._settings.raw_lane

    @property
    def unknown_lane(self) -> str:
        return self._settings.unknown_lane

    def from_log_module(self, module: Optional[str]) -> Optional[str]:
        """優先順位 1: ログのモジュール欄。"""
        if not module:
            return None
        cached = self._log_cache.get(module)
        if cached is not None:
            return cached
        result: Optional[str] = None
        for pattern, name in self._aliases:
            if pattern.search(module):
                result = name
                break
        if result is None:
            result = module
            for sep in self._settings.prefix_separators:
                if sep and sep in result:
                    head = result.split(sep, 1)[0].strip()
                    if head:
                        result = head
                        break
            result = result.strip()
        self._log_cache[module] = result
        if result:
            self._log_lanes.setdefault(result.lower(), result)
        return result or None

    def from_file_name(self, file_name: Optional[str]) -> Optional[str]:
        """優先順位 2: ファイル名ルール。"""
        if not file_name or not self._file_rules:
            return None
        base = file_name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        for pattern, name in self._file_rules:
            if fnmatch.fnmatchcase(base, pattern):
                return name
        return None

    def from_path(self, path: Optional[str]) -> Optional[str]:
        """優先順位 3: ディレクトリルール。"""
        if not path:
            return None
        norm = path.replace("\\", "/")
        for rule in self._dir_rules:
            m = rule.search(norm)
            if m is not None and m.groups() and m.group(1):
                return m.group(1)
        parent = norm.rsplit("/", 2)
        return parent[-2] if len(parent) >= 2 else None

    def module_for_path(self, path: str) -> str:
        """関数定義のモジュール（ファイル名 → ディレクトリ）。"""
        return (
            self.from_file_name(path)
            or self.from_path(path)
            or self._settings.unknown_lane
        )

    def lane_for(self, entry: LogEntry, function: Optional[FunctionInfo]) -> str:
        lane = self.from_log_module(entry.module_name)
        if lane:
            return lane
        lane = self.from_file_name(entry.file_name)
        if lane:
            return self._log_lanes.get(lane.lower(), lane)
        if function is not None:
            return self._log_lanes.get(function.module.lower(), function.module)
        if entry.kind is LogKind.RAW:
            return self._settings.raw_lane
        return self._settings.unknown_lane
