"""設定の読み込み。既定設定（default_config.toml）にユーザー設定を重ねる。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULT_CONFIG_PATH = Path(__file__).with_name("default_config.toml")

ASYNC_KINDS = (
    "thread",
    "event_send",
    "queue_send",
    "queue_receive",
    "callback_register",
)


@dataclass(frozen=True)
class LogSettings:
    encoding: str = "utf-8"
    errors: str = "replace"


@dataclass(frozen=True)
class SourceSettings:
    path_prefix_strip: tuple[str, ...] = ("Input/",)
    extensions: tuple[str, ...] = (".c", ".cpp", ".h", ".hpp")
    exclude_patterns: tuple[str, ...] = ()
    # ソースフォルダ読込み時の文字コード（auto = UTF-8 → CP932 の順に判定）
    encoding: str = "auto"


@dataclass(frozen=True)
class AnalyzerSettings:
    backend: str = "auto"
    jobs: int = 0
    c_args: tuple[str, ...] = ("-std=gnu11",)
    cxx_args: tuple[str, ...] = ("-std=gnu++17",)
    defines: tuple[str, ...] = ()
    cache_dir: str = ".lv_cache"
    exclude_callees: tuple[str, ...] = ()


@dataclass(frozen=True)
class AsyncApiSpec:
    name: str
    kind: str
    arg: int
    handle_arg: Optional[int] = None


@dataclass(frozen=True)
class ModuleAlias:
    pattern: str
    module: str


@dataclass(frozen=True)
class FileRule:
    pattern: str
    module: str


@dataclass(frozen=True)
class ModuleSettings:
    prefix_separators: tuple[str, ...] = (":",)
    aliases: tuple[ModuleAlias, ...] = ()
    file_rules: tuple[FileRule, ...] = ()
    directory_rules: tuple[str, ...] = ()
    raw_lane: str = "(other)"
    unknown_lane: str = "(unknown)"


@dataclass(frozen=True)
class SequenceSettings:
    max_caller_hops: int = 3
    sync_window_ms: int = 1000
    async_window_ms: int = 10000
    callback_window_ms: int = 10000


@dataclass(frozen=True)
class RenderSettings:
    embed_sources: str = "logged"
    max_message_length: int = 4000


@dataclass(frozen=True)
class Settings:
    log: LogSettings = field(default_factory=LogSettings)
    source: SourceSettings = field(default_factory=SourceSettings)
    analyzer: AnalyzerSettings = field(default_factory=AnalyzerSettings)
    async_apis: tuple[AsyncApiSpec, ...] = ()
    modules: ModuleSettings = field(default_factory=ModuleSettings)
    sequence: SequenceSettings = field(default_factory=SequenceSettings)
    render: RenderSettings = field(default_factory=RenderSettings)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def settings_from_dict(data: dict[str, Any]) -> Settings:
    log = data.get("log", {})
    source = data.get("source", {})
    analyzer = data.get("analyzer", {})
    modules = data.get("modules", {})
    sequence = data.get("sequence", {})
    render = data.get("render", {})

    apis: list[AsyncApiSpec] = []
    for item in data.get("async_apis", []):
        kind = str(item["kind"])
        if kind not in ASYNC_KINDS:
            raise ValueError(f"未知の async_apis.kind: {kind}")
        handle = item.get("handle_arg")
        apis.append(
            AsyncApiSpec(
                name=str(item["name"]),
                kind=kind,
                arg=int(item.get("arg", 0)),
                handle_arg=None if handle is None else int(handle),
            )
        )

    return Settings(
        log=LogSettings(
            encoding=str(log.get("encoding", "utf-8")),
            errors=str(log.get("errors", "replace")),
        ),
        source=SourceSettings(
            path_prefix_strip=_tuple(source.get("path_prefix_strip")),
            extensions=tuple(e.lower() for e in _tuple(source.get("extensions"))),
            exclude_patterns=_tuple(source.get("exclude_patterns")),
            encoding=str(source.get("encoding", "auto")),
        ),
        analyzer=AnalyzerSettings(
            backend=str(analyzer.get("backend", "auto")),
            jobs=int(analyzer.get("jobs", 0)),
            c_args=_tuple(analyzer.get("c_args")),
            cxx_args=_tuple(analyzer.get("cxx_args")),
            defines=_tuple(analyzer.get("defines")),
            cache_dir=str(analyzer.get("cache_dir", ".lv_cache")),
            exclude_callees=_tuple(analyzer.get("exclude_callees")),
        ),
        async_apis=tuple(apis),
        modules=ModuleSettings(
            prefix_separators=_tuple(modules.get("prefix_separators")),
            aliases=tuple(
                ModuleAlias(str(a["pattern"]), str(a["module"]))
                for a in modules.get("aliases", [])
            ),
            file_rules=tuple(
                FileRule(str(r["pattern"]), str(r["module"]))
                for r in modules.get("file_rules", [])
            ),
            directory_rules=_tuple(modules.get("directory_rules")),
            raw_lane=str(modules.get("raw_lane", "(other)")),
            unknown_lane=str(modules.get("unknown_lane", "(unknown)")),
        ),
        sequence=SequenceSettings(
            max_caller_hops=int(sequence.get("max_caller_hops", 3)),
            sync_window_ms=int(sequence.get("sync_window_ms", 1000)),
            async_window_ms=int(sequence.get("async_window_ms", 10000)),
            callback_window_ms=int(sequence.get("callback_window_ms", 10000)),
        ),
        render=RenderSettings(
            embed_sources=str(render.get("embed_sources", "logged")),
            max_message_length=int(render.get("max_message_length", 4000)),
        ),
    )


def load_settings(path: Optional[Path] = None) -> Settings:
    """既定設定を読み、path が指定されていれば上書きする。"""
    with DEFAULT_CONFIG_PATH.open("rb") as fp:
        data = tomllib.load(fp)
    if path is not None:
        with Path(path).open("rb") as fp:
            data = _deep_merge(data, tomllib.load(fp))
    return settings_from_dict(data)
