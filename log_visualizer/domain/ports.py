"""ポート（ユースケースが外側の実装に要求するインターフェース）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Protocol

from .models import CallGraph, LogEntry, SequenceEvent, SourceFile


class LogReader(Protocol):
    """ログファイルを LogEntry のストリームとして読む。"""

    def parse(self, path: Path) -> Iterator[LogEntry]:
        ...


class SourceRepository(Protocol):
    """ソースコード群をストリームとして提供する。"""

    def fingerprint(self) -> str:
        """内容のハッシュ（キャッシュキー用）。"""
        ...

    def location(self) -> str:
        """入力の場所（ダンプファイルまたはソースフォルダの絶対パス）。"""
        ...

    def local_root(self) -> Optional[Path]:
        """ディスク上のソースルート。ダンプなど実ファイルがない場合は None。"""
        ...

    def signatures(self) -> dict[str, str]:
        """解析対象ファイルごとの変更検知用シグネチャ（差分キャッシュ用）。"""
        ...

    def iter_files(self) -> Iterator[SourceFile]:
        """解析対象（除外パターン適用後）のファイル。"""
        ...

    def iter_all(self, only: Optional[set[str]] = None) -> Iterator[SourceFile]:
        """除外パターンを適用しない全ファイル（only 指定時はそのパスのみ）。"""
        ...

    def iter_selected(self, paths: Iterable[str]) -> Iterator[SourceFile]:
        ...


class CallGraphProvider(Protocol):
    """ソースコード群から CallGraph を構築する。"""

    def build_callgraph(self) -> CallGraph:
        ...

    def describe(self) -> dict[str, Any]:
        """構築結果の概要（解析方式・件数など）。build_callgraph 後に呼ぶ。"""
        ...


class CallGraphWriter(Protocol):
    def write(self, graph: CallGraph, out_dir: Path) -> list[Path]:
        ...


class SequenceGenerator(Protocol):
    def build(self, entries: Iterable[LogEntry]) -> Iterator[SequenceEvent]:
        ...


class SequenceRenderer(Protocol):
    def render(
        self,
        events: Iterable[SequenceEvent],
        graph: CallGraph,
        sources: SourceRepository,
        out_path: Path,
        title: Optional[str] = None,
    ) -> Path:
        ...
