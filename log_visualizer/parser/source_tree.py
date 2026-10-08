"""ディスク上のソースツリーの再帰読込み（大規模対応）。

- os.walk で再帰的に走査し、除外パターンに一致するフォルダは中に入らない
- 文字コードは UTF-8 → CP932（Shift_JIS）の順に自動判定
- ファイル内容は必要になったときだけ読む（signatures() は stat のみ）
- パスはルートからの相対パス（"/" 区切り）。ログの行番号は実ファイルとそのまま一致する
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path
from typing import Iterable, Iterator, Optional

from ..config.settings import SourceSettings
from ..domain.models import SourceFile

# 常に走査しないフォルダ（VCS・仮想環境・キャッシュ）
ALWAYS_SKIP_DIRS = frozenset(
    {".git", ".svn", ".hg", ".venv", "venv", "node_modules", "__pycache__", ".lv_cache", ".vs", ".idea"}
)
_PROBE = "__lv_probe__"


def decode_source(data: bytes, encoding: str = "auto") -> str:
    """ソースのバイト列を文字列にする。auto は UTF-8 → CP932 → Latin-1 の順。"""
    if encoding != "auto":
        return data.decode(encoding, errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    for candidate in ("utf-8", "cp932"):
        try:
            return data.decode(candidate)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def read_source(path: Path, encoding: str = "auto") -> str:
    # 改行は \n に統一（行番号は変わらない）
    text = decode_source(path.read_bytes(), encoding)
    return text.replace("\r\n", "\n").replace("\r", "\n")


class DirectorySourceReader:
    """ソースフォルダを SourceFile のストリームとして提供する（SourceRepository）。"""

    def __init__(self, root: Path, settings: SourceSettings) -> None:
        self._root = Path(root).resolve()
        self._encoding = settings.encoding
        self._extensions = tuple(e.lower() for e in settings.extensions)
        self._excludes = tuple(p.lower() for p in settings.exclude_patterns)

    # -- SourceRepository ------------------------------------------------
    def location(self) -> str:
        return str(self._root)

    def local_root(self) -> Optional[Path]:
        return self._root

    def fingerprint(self) -> str:
        """ファイル一覧とサイズ・更新時刻のハッシュ。"""
        digest = hashlib.sha256(str(self._root).encode("utf-8"))
        for rel, sig in sorted(self._stat_all(apply_excludes=False).items()):
            digest.update(f"{rel}:{sig}\n".encode("utf-8"))
        return digest.hexdigest()[:24]

    def signatures(self) -> dict[str, str]:
        """解析対象ファイルごとの "サイズ:更新時刻"（内容は読まない）。"""
        return self._stat_all(apply_excludes=True)

    def iter_files(self) -> Iterator[SourceFile]:
        for rel in self._walk(apply_excludes=True):
            yield self._load(rel)

    def iter_all(self, only: Optional[set[str]] = None) -> Iterator[SourceFile]:
        if only is not None:
            yield from self.iter_selected(only)
            return
        for rel in self._walk(apply_excludes=False):
            yield self._load(rel)

    def iter_selected(self, paths: Iterable[str]) -> Iterator[SourceFile]:
        for rel in sorted(set(paths)):
            if (self._root / rel).is_file():
                yield self._load(rel)

    # -- 内部 ------------------------------------------------------------
    def _load(self, rel: str) -> SourceFile:
        return SourceFile(rel, read_source(self._root / rel, self._encoding))

    def _excluded(self, rel: str) -> bool:
        lower = rel.lower()
        return any(fnmatch.fnmatchcase(lower, pat) for pat in self._excludes)

    def _walk(self, apply_excludes: bool) -> Iterator[str]:
        """相対パスを辞書順に列挙する。"""
        for dirpath, dirnames, filenames in os.walk(self._root):
            rel_dir = os.path.relpath(dirpath, self._root).replace(os.sep, "/")
            rel_dir = "" if rel_dir == "." else rel_dir + "/"
            kept = []
            for name in sorted(dirnames):
                if name in ALWAYS_SKIP_DIRS:
                    continue
                # フォルダ単位の除外（"*/test/*" など）は中に入らない
                if apply_excludes and self._excluded(f"{rel_dir}{name}/{_PROBE}"):
                    continue
                kept.append(name)
            dirnames[:] = kept
            for name in sorted(filenames):
                if not name.lower().endswith(self._extensions):
                    continue
                rel = rel_dir + name
                if apply_excludes and self._excluded(rel):
                    continue
                yield rel

    def _stat_all(self, apply_excludes: bool) -> dict[str, str]:
        result: dict[str, str] = {}
        for rel in self._walk(apply_excludes):
            try:
                st = (self._root / rel).stat()
            except OSError:
                continue
            result[rel] = f"{st.st_size}:{st.st_mtime_ns}"
        return result
