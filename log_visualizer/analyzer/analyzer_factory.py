"""解析器の選択とキャッシュ。"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Iterator, Optional, Protocol

from .. import __version__
from ..config.settings import Settings
from ..domain.models import SourceFile
from ..domain.ports import SourceRepository
from .clang_analyzer import ClangSourceAnalyzer, libclang_available
from .raw_model import RawFileAnalysis
from .regex_analyzer import RegexSourceAnalyzer

LOG = logging.getLogger(__name__)

BACKENDS = ("auto", "clang", "regex")


class SourceAnalyzer(Protocol):
    name: str

    def analyze_files(self, files: Iterable[SourceFile]) -> Iterator[RawFileAnalysis]:
        ...


def resolve_backend(requested: str) -> str:
    if requested not in BACKENDS:
        raise ValueError(f"未知の解析バックエンド: {requested}（{', '.join(BACKENDS)}）")
    if requested == "auto":
        return "clang" if libclang_available() else "regex"
    if requested == "clang" and not libclang_available():
        raise RuntimeError(
            "libclang が利用できません。`pip install libclang` を実行するか "
            "--backend regex を指定してください。"
        )
    return requested


def create_analyzer(
    settings: Settings,
    repository: SourceRepository,
    backend: str,
    work_root: Path,
) -> SourceAnalyzer:
    if backend == "clang":
        work_dir = work_root / f"clang_work_{repository.fingerprint()}"
        return ClangSourceAnalyzer(settings, work_dir, repository.iter_all())
    return RegexSourceAnalyzer(settings)


class CachedSourceAnalysis:
    """解析結果（RawFileAnalysis 群）をキャッシュする。

    キー = ツールバージョン + バックエンド + ソース内容 + 解析関連設定。
    """

    def __init__(self, settings: Settings, repository: SourceRepository) -> None:
        self._settings = settings
        self._repository = repository
        self._cache_dir = Path(settings.analyzer.cache_dir)

    def cache_path(self, backend: str) -> Path:
        key = "_".join(
            [__version__, backend, self._repository.fingerprint(), self._settings_key()]
        )
        return self._cache_dir / f"raw_{key}.json.gz"

    def _settings_key(self) -> str:
        """解析結果に影響する設定だけのハッシュ（並列数やキャッシュ先は含めない）。"""
        a = self._settings.analyzer
        relevant = {
            "source": asdict(self._settings.source),
            "async_apis": [asdict(x) for x in self._settings.async_apis],
            "c_args": a.c_args,
            "cxx_args": a.cxx_args,
            "defines": a.defines,
        }
        text = json.dumps(relevant, sort_keys=True, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def load(
        self, backend: Optional[str] = None, use_cache: bool = True
    ) -> tuple[str, list[RawFileAnalysis]]:
        name = resolve_backend(backend or self._settings.analyzer.backend)
        path = self.cache_path(name)
        if use_cache and path.exists():
            LOG.info("解析キャッシュを使用: %s", path)
            with gzip.open(path, "rt", encoding="utf-8") as fp:
                data = json.load(fp)
            return name, [RawFileAnalysis.from_dict(d) for d in data]

        analyzer = create_analyzer(self._settings, self._repository, name, self._cache_dir)
        LOG.info("ソース解析を開始（backend=%s）", name)
        results: list[RawFileAnalysis] = []
        for count, analysis in enumerate(
            analyzer.analyze_files(self._repository.iter_files()), start=1
        ):
            results.append(analysis)
            if count % 200 == 0:
                LOG.info("  %d ファイル解析済み", count)
        if use_cache:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as fp:
                json.dump([r.to_dict() for r in results], fp, ensure_ascii=False)
            tmp.replace(path)
        return name, results
