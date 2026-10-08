"""解析器の選択とキャッシュ。"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Protocol

from .. import __version__
from ..config.settings import Settings
from ..domain.models import SourceFile
from ..domain.ports import SourceRepository
from .clang_analyzer import ClangSourceAnalyzer, libclang_available
from .raw_model import RawFileAnalysis, merge_analyses
from .regex_analyzer import RegexSourceAnalyzer

LOG = logging.getLogger(__name__)

BACKENDS = ("auto", "clang", "regex")


class SourceAnalyzer(Protocol):
    name: str

    def is_unit(self, path: str) -> bool:
        """そのファイルが解析単位（clang なら翻訳単位）になるか。"""
        ...

    def analyze_units(
        self, files: Iterable[SourceFile]
    ) -> Iterator[tuple[str, list[RawFileAnalysis]]]:
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
        # 作業フォルダは入力の場所ごとに 1 つ（内容が変われば中のスタブを作り直す）
        location = hashlib.sha256(repository.location().encode("utf-8")).hexdigest()[:12]
        return ClangSourceAnalyzer(
            settings,
            work_root / f"clang_work_{location}",
            repository.iter_all,
            source_root=repository.local_root(),
            fingerprint=repository.fingerprint,
        )
    return RegexSourceAnalyzer(settings)


class CachedSourceAnalysis:
    """ファイル単位の差分キャッシュ付きでソースを解析する。

    - キャッシュは「入力の場所 + 解析方式 + 解析に影響する設定」ごとに 1 ファイル
    - 各ファイルのシグネチャ（ダンプ: 内容ハッシュ / フォルダ: サイズ・更新時刻）が
      変わったものだけを再解析する
    - SAVE_EVERY 件ごとに途中保存するため、中断しても続きから再開できる
    - clang でヘッダだけを変更した場合、それを include する .c は再解析されない。
      その場合は --rebuild-cache を使う
    """

    FORMAT = 2
    SAVE_EVERY = 300

    def __init__(self, settings: Settings, repository: SourceRepository) -> None:
        self._settings = settings
        self._repository = repository
        self._cache_dir = Path(settings.analyzer.cache_dir)

    def cache_path(self, backend: str) -> Path:
        location = hashlib.sha256(self._repository.location().encode("utf-8")).hexdigest()[:12]
        key = "_".join([__version__, backend, location, self._settings_key()])
        return self._cache_dir / f"units_{key}.json.gz"

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
        self,
        backend: Optional[str] = None,
        use_cache: bool = True,
        rebuild: bool = False,
    ) -> tuple[str, list[RawFileAnalysis]]:
        """解析結果を返す。use_cache=False ならキャッシュを読み書きしない。"""
        name = resolve_backend(backend or self._settings.analyzer.backend)
        path = self.cache_path(name)
        LOG.info("ソースの一覧を取得中: %s", self._repository.location())
        signatures = self._repository.signatures()
        units: dict[str, dict[str, Any]] = {}
        if use_cache and not rebuild:
            units = self._read(path)
        # 削除されたファイルを落とす
        units = {p: u for p, u in units.items() if p in signatures}
        changed = sorted(p for p, sig in signatures.items() if units.get(p, {}).get("sig") != sig)
        LOG.info(
            "対象 %d ファイル（キャッシュ済み %d / 解析が必要 %d, backend=%s）",
            len(signatures), len(signatures) - len(changed), len(changed), name,
        )
        if changed:
            self._analyze(name, changed, signatures, units, path if use_cache else None)
        results = merge_analyses(
            RawFileAnalysis.from_dict(d) for u in units.values() for d in u["results"]
        )
        return name, results

    def _analyze(
        self,
        backend: str,
        changed: list[str],
        signatures: dict[str, str],
        units: dict[str, dict[str, Any]],
        save_path: Optional[Path],
    ) -> None:
        analyzer = create_analyzer(self._settings, self._repository, backend, self._cache_dir)
        total = sum(1 for p in changed if analyzer.is_unit(p))
        started = time.perf_counter()
        done = 0
        files = self._repository.iter_selected(changed)
        for rel, results in analyzer.analyze_units(files):
            units[rel] = {"sig": signatures.get(rel, ""), "results": [r.to_dict() for r in results]}
            done += 1
            if done % 100 == 0 or done == total:
                elapsed = time.perf_counter() - started
                remain = elapsed / done * (total - done) if done else 0
                LOG.info("  解析 %d / %d（残り約 %d 秒）", done, total, int(remain))
            if save_path is not None and done % self.SAVE_EVERY == 0:
                self._write(save_path, units)
        # 解析単位にならなかったファイル（clang でのヘッダ等）も「確認済み」として記録
        for rel in changed:
            units.setdefault(rel, {"sig": signatures[rel], "results": []})
        if save_path is not None:
            self._write(save_path, units)

    def _read(self, path: Path) -> dict[str, dict[str, Any]]:
        if not path.exists():
            return {}
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, ValueError) as exc:
            LOG.warning("キャッシュを読めないため作り直します: %s (%s)", path, exc)
            return {}
        if data.get("format") != self.FORMAT:
            return {}
        LOG.info("解析キャッシュを使用: %s", path)
        units: dict[str, dict[str, Any]] = data.get("units", {})
        return units

    def _write(self, path: Path, units: dict[str, dict[str, Any]]) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=5) as fp:
            json.dump(
                {"format": self.FORMAT, "location": self._repository.location(), "units": units},
                fp,
                ensure_ascii=False,
            )
        tmp.replace(path)
