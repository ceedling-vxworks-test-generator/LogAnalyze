"""ソースダンプ（複数ソースを 1 ファイルに連結した形式）の逐次読込み。

    ===== FILE BEGIN =====
    Path: Input\\src\\foo\\bar.c
    ===== CONTENT BEGIN =====
    ...ソース...
    ===== CONTENT END =====
    ===== FILE END =====
"""

from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path
from typing import Iterable, Iterator, Optional

from ..config.settings import SourceSettings
from ..domain.models import SourceFile

FILE_BEGIN = "===== FILE BEGIN ====="
FILE_END = "===== FILE END ====="
CONTENT_BEGIN = "===== CONTENT BEGIN ====="
CONTENT_END = "===== CONTENT END ====="
PATH_PREFIX = "Path:"


class PathFilter:
    """パス正規化と除外判定。"""

    def __init__(self, settings: SourceSettings) -> None:
        self._strip = tuple(p.replace("\\", "/") for p in settings.path_prefix_strip)
        self._extensions = tuple(e.lower() for e in settings.extensions)
        self._excludes = tuple(p.lower() for p in settings.exclude_patterns)

    def normalize(self, raw: str) -> str:
        path = raw.strip().replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        for prefix in self._strip:
            if prefix and path.lower().startswith(prefix.lower()):
                path = path[len(prefix):]
                break
        return path.lstrip("/")

    def accepts(self, path: str) -> bool:
        lower = path.lower()
        if self._extensions and not lower.endswith(self._extensions):
            return False
        return not any(fnmatch.fnmatchcase(lower, pat) for pat in self._excludes)


class SourceDumpReader:
    """ソースダンプを SourceFile のストリームとして提供する（SourceRepository）。"""

    def __init__(
        self,
        dump_path: Path,
        settings: SourceSettings,
        encoding: str = "utf-8",
        errors: str = "replace",
    ) -> None:
        self._path = Path(dump_path)
        self._filter = PathFilter(settings)
        self._encoding = encoding
        self._errors = errors

    @property
    def path(self) -> Path:
        return self._path

    def fingerprint(self) -> str:
        """ダンプ内容の SHA-256（キャッシュキー用）。"""
        digest = hashlib.sha256()
        with self._path.open("rb") as fp:
            for chunk in iter(lambda: fp.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()[:24]

    def location(self) -> str:
        """キャッシュを区別するための入力の場所。"""
        return str(self._path.resolve())

    def local_root(self) -> Optional[Path]:
        """ディスク上のソースルート（ダンプなので None）。"""
        return None

    def signatures(self) -> dict[str, str]:
        """解析対象ファイルごとの内容ハッシュ（差分キャッシュ用）。"""
        return {
            f.path: hashlib.sha1(f.content.encode("utf-8")).hexdigest()
            for f in self.iter_files()
        }

    def iter_files(self) -> Iterator[SourceFile]:
        """除外パターンを適用したファイルを順に返す。"""
        for source in self.iter_all():
            if self._filter.accepts(source.path):
                yield source

    def iter_selected(self, paths: Iterable[str]) -> Iterator[SourceFile]:
        """指定パスのファイルだけを返す（ソースビューア埋込み用）。"""
        wanted = set(paths)
        if not wanted:
            return
        for source in self.iter_all(wanted):
            yield source

    def iter_all(self, only: Optional[set[str]] = None) -> Iterator[SourceFile]:
        with self._path.open(
            "r", encoding=self._encoding, errors=self._errors, newline=""
        ) as fp:
            yield from parse_dump_lines(fp, self._filter, only)


def parse_dump_lines(
    lines: Iterable[str],
    path_filter: PathFilter,
    only: Optional[set[str]] = None,
) -> Iterator[SourceFile]:
    """ダンプ行列を解析する。

    本文中に終端マーカーと同じ行が現れても誤認しないよう、
    CONTENT END の直後に FILE END が続く場合のみ終端とみなす。
    """
    path: Optional[str] = None
    buf: Optional[list[str]] = None
    held_end: Optional[str] = None
    skip = False

    for raw in lines:
        line = raw.rstrip("\r\n")

        if buf is not None:
            if held_end is not None:
                if line == FILE_END:
                    if path is not None and not skip:
                        yield SourceFile(path, "".join(buf))
                    path, buf, held_end, skip = None, None, None, False
                    continue
                if not skip:
                    buf.append(held_end)
                held_end = None
            if line == CONTENT_END:
                held_end = raw
                continue
            if not skip:
                buf.append(raw if raw.endswith("\n") else raw + "\n")
            continue

        if line == FILE_BEGIN:
            path = None
        elif line.startswith(PATH_PREFIX):
            path = path_filter.normalize(line[len(PATH_PREFIX):])
            skip = only is not None and path not in only
        elif line == CONTENT_BEGIN:
            buf = []
            held_end = None

    if buf is not None and path is not None and not skip:
        yield SourceFile(path, "".join(buf))


class EmptySourceRepository:
    """ソースなしで実行する場合のリポジトリ。"""

    def fingerprint(self) -> str:
        return "empty"

    def location(self) -> str:
        return "empty"

    def local_root(self) -> Optional[Path]:
        return None

    def signatures(self) -> dict[str, str]:
        return {}

    def iter_files(self) -> Iterator[SourceFile]:
        return iter(())

    def iter_all(self, only: Optional[set[str]] = None) -> Iterator[SourceFile]:
        return iter(())

    def iter_selected(self, paths: Iterable[str]) -> Iterator[SourceFile]:
        return iter(())
