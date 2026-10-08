"""テスト共通フィクスチャ。"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Iterable

import pytest

from log_visualizer.analyzer.module_resolver import ModuleResolver
from log_visualizer.analyzer.raw_model import RawFileAnalysis
from log_visualizer.analyzer.regex_analyzer import RegexSourceAnalyzer
from log_visualizer.callgraph.builder import CallGraphBuilder
from log_visualizer.config.settings import Settings, load_settings
from log_visualizer.domain.models import CallGraph, SourceFile

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings()


@pytest.fixture()
def tmp_settings(settings: Settings, tmp_path: Path) -> Settings:
    """キャッシュを tmp_path に向けた設定。"""
    analyzer = dataclasses.replace(settings.analyzer, cache_dir=str(tmp_path / "cache"))
    return dataclasses.replace(settings, analyzer=analyzer)


@pytest.fixture(scope="session")
def sample_log() -> Path:
    return SAMPLE_DIR / "sample.log"


@pytest.fixture(scope="session")
def sample_dump() -> Path:
    return SAMPLE_DIR / "sample_source_dump.txt"


@pytest.fixture(scope="session")
def sample_src() -> Path:
    """サンプルの実ソースフォルダ（sample.log の行番号と一致する）。"""
    return SAMPLE_DIR / "sample_src"


def analyze_regex(settings: Settings, files: Iterable[SourceFile]) -> list[RawFileAnalysis]:
    return [r for _, rs in RegexSourceAnalyzer(settings).analyze_units(files) for r in rs]


def build_graph(settings: Settings, files: Iterable[SourceFile]) -> CallGraph:
    builder = CallGraphBuilder(
        ModuleResolver(settings.modules), settings.analyzer.exclude_callees
    )
    return builder.build(analyze_regex(settings, files))


def log_line(
    ts: str,
    level: str,
    module: str,
    message: str,
    file: str | None = None,
    line: int | None = None,
    func: str | None = None,
) -> str:
    """utility_log 書式のログ行を作る。"""
    text = f"[2026-10-07 {ts}] [0] {level:<5} {module:<23} {message}"
    if file is not None:
        text += f" ({file}:{line} {func})"
    return text
