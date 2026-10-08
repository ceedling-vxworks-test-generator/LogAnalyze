"""libclang による C/C++ 解析器。

- ソースダンプを作業ディレクトリへ展開し、ヘッダのあるディレクトリをすべて -I に指定する。
- pip 版 libclang は標準ヘッダを持たないため、プロジェクト内で解決できない
  #include に空のスタブを作り、基本型はプレリュード（-include）で与える。
- 関数定義・呼び出し・関数ポインタ呼び出しは AST から取得し、
  非同期 API 呼び出しと関数参照（コールバック登録）は本体テキストから抽出する
  （標準ライブラリ型が不完全でも取りこぼさないため）。
- 翻訳単位ごとにプロセス並列で解析する。
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from ..config.settings import AsyncApiSpec, Settings
from ..domain.models import SourceFile
from .async_rules import AsyncApiRules
from .body_extractor import BodyExtractor
from .raw_model import (
    CALL_DIRECT,
    CALL_FIELD,
    CALL_POINTER,
    RawCall,
    RawFileAnalysis,
    RawFunction,
)
from .text_utils import LineIndex, prepare_source

TU_EXTENSIONS_C = (".c",)
TU_EXTENSIONS_CXX = (".cc", ".cpp", ".cxx")
HEADER_EXTENSIONS = (".h", ".hh", ".hpp", ".hxx", ".inl")

_INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*[<"]([^>"]+)[>"]', re.MULTILINE)

PRELUDE = """\
/* log_visualizer: libclang 用プレリュード（標準ヘッダの代替） */
#ifndef LV_PRELUDE_H
#define LV_PRELUDE_H
typedef signed char int8_t;
typedef unsigned char uint8_t;
typedef short int16_t;
typedef unsigned short uint16_t;
typedef int int32_t;
typedef unsigned int uint32_t;
typedef long long int64_t;
typedef unsigned long long uint64_t;
typedef long intptr_t;
typedef unsigned long uintptr_t;
typedef unsigned long size_t;
typedef long ssize_t;
typedef long ptrdiff_t;
typedef long off_t;
typedef long time_t;
typedef int pid_t;
typedef unsigned long pthread_t;
typedef struct { long __lv[8]; } pthread_mutex_t;
typedef struct { long __lv[8]; } pthread_cond_t;
typedef struct { long __lv[8]; } pthread_attr_t;
typedef struct { long __lv[4]; } sem_t;
typedef struct _IO_FILE FILE;
typedef __builtin_va_list va_list;
#define va_start(v, l) __builtin_va_start(v, l)
#define va_end(v) __builtin_va_end(v)
#define va_arg(v, t) __builtin_va_arg(v, t)
#ifndef NULL
#ifdef __cplusplus
#define NULL nullptr
#else
#define NULL ((void*)0)
#endif
#endif
#ifndef __cplusplus
#define bool _Bool
#define true 1
#define false 0
#endif
#define UINT8_MAX 255
#define UINT16_MAX 65535
#define UINT32_MAX 4294967295u
#define INT32_MAX 2147483647
#define SIZE_MAX (~(size_t)0)
#define EOK 0
#endif
"""


class ClangWorkspace:
    """libclang 用の作業ディレクトリ（展開済みソース・スタブ・プレリュード）。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.src = self.root / "src"
        self.stubs = self.root / "stubs"
        self.prelude = self.root / "lv_prelude.h"
        self.include_dirs: list[str] = []

    @property
    def ready_marker(self) -> Path:
        return self.root / ".ready"

    def prepare(self, files: Iterable[SourceFile]) -> None:
        include_dirs_file = self.root / "include_dirs.txt"
        if self.ready_marker.exists() and include_dirs_file.exists():
            self.include_dirs = include_dirs_file.read_text(encoding="utf-8").splitlines()
            return
        self.root.mkdir(parents=True, exist_ok=True)
        header_dirs: set[str] = set()
        known_suffixes: set[str] = set()
        includes: set[str] = set()
        for source in files:
            dest = self.src / source.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(source.content, encoding="utf-8", newline="")
            lower = source.path.lower()
            parts = source.path.split("/")
            for i in range(len(parts)):
                known_suffixes.add("/".join(parts[i:]).lower())
            if lower.endswith(HEADER_EXTENSIONS):
                header_dirs.add(str(dest.parent))
            includes.update(_INCLUDE.findall(source.content))
        self.stubs.mkdir(parents=True, exist_ok=True)
        for inc in includes:
            norm = inc.replace("\\", "/").lstrip("./")
            if norm.lower() in known_suffixes or ".." in norm:
                continue
            stub = self.stubs / norm
            stub.parent.mkdir(parents=True, exist_ok=True)
            if not stub.exists():
                stub.write_text("/* log_visualizer stub */\n", encoding="utf-8")
        self.prelude.write_text(PRELUDE, encoding="utf-8")
        self.include_dirs = sorted(header_dirs)
        include_dirs_file.write_text("\n".join(self.include_dirs), encoding="utf-8")
        self.ready_marker.write_text("ok", encoding="utf-8")


# ---------------------------------------------------------------------------
# ワーカー（別プロセスで実行される）
# ---------------------------------------------------------------------------

_WORKER: dict[str, Any] = {}


def _worker_init(
    src_root: str, base_args: list[str], specs: list[tuple[str, str, int, Optional[int]]]
) -> None:
    import clang.cindex as ci

    _WORKER["ci"] = ci
    _WORKER["index"] = ci.Index.create()
    _WORKER["src_root"] = os.path.normcase(os.path.abspath(src_root))
    _WORKER["base_args"] = base_args
    rules = AsyncApiRules(
        AsyncApiSpec(name=n, kind=k, arg=a, handle_arg=h) for n, k, a, h in specs
    )
    _WORKER["rules"] = rules
    _WORKER["extractor"] = BodyExtractor(rules)


def _worker_parse(job: tuple[str, list[str]]) -> list[dict[str, Any]]:
    path, lang_args = job
    try:
        return [a.to_dict() for a in _ClangTuParser().parse(path, lang_args)]
    except Exception as exc:  # 1 ファイルの失敗で全体を止めない
        rel = os.path.relpath(path, _WORKER["src_root"]).replace(os.sep, "/")
        return [RawFileAnalysis(path=rel, errors=[f"{type(exc).__name__}: {exc}"]).to_dict()]


class _ClangTuParser:
    """1 翻訳単位の解析（ワーカー内）。"""

    def __init__(self) -> None:
        self.ci = _WORKER["ci"]
        self.src_root: str = _WORKER["src_root"]
        self.rules: AsyncApiRules = _WORKER["rules"]
        self.extractor: BodyExtractor = _WORKER["extractor"]
        self.results: dict[str, RawFileAnalysis] = {}
        self.texts: dict[str, tuple[str, LineIndex]] = {}
        self.seen: set[tuple[str, str, int]] = set()

    def parse(self, path: str, lang_args: list[str]) -> list[RawFileAnalysis]:
        ci = self.ci
        tu = _WORKER["index"].parse(
            path,
            args=lang_args + _WORKER["base_args"],
            options=ci.TranslationUnit.PARSE_INCOMPLETE,
        )
        main_rel = self._rel(path)
        main = self._result(main_rel) if main_rel else None
        if main is not None:
            fatal = [
                d.spelling for d in tu.diagnostics if d.severity >= ci.Diagnostic.Fatal
            ]
            main.errors.extend(fatal[:5])
        self._visit(tu.cursor)
        for rel, analysis in self.results.items():
            text, lines = self._text(rel)
            ranges: list[tuple[int, int]] = []
            for fn in analysis.functions:
                ranges.append(
                    (lines.offset_of_line(fn.start_line), lines.offset_of_line(fn.end_line + 1))
                )
            if rel == main_rel:
                BodyExtractor.file_scope_refs(text, lines, analysis, ranges)
        return list(self.results.values())

    # -- helpers --------------------------------------------------------
    def _rel(self, file_name: str) -> Optional[str]:
        full = os.path.normcase(os.path.abspath(file_name))
        if not full.startswith(self.src_root + os.sep):
            return None
        return os.path.relpath(file_name, self.src_root).replace(os.sep, "/")

    def _result(self, rel: str) -> RawFileAnalysis:
        result = self.results.get(rel)
        if result is None:
            result = RawFileAnalysis(path=rel)
            self.results[rel] = result
        return result

    def _text(self, rel: str) -> tuple[str, LineIndex]:
        cached = self.texts.get(rel)
        if cached is None:
            raw = Path(self.src_root, rel).read_text(encoding="utf-8", errors="replace")
            text = prepare_source(raw)
            cached = (text, LineIndex(text))
            self.texts[rel] = cached
        return cached

    def _visit(self, cursor: Any) -> None:
        K = self.ci.CursorKind
        containers = {
            K.NAMESPACE,
            K.LINKAGE_SPEC,
            K.UNEXPOSED_DECL,
            K.CLASS_DECL,
            K.STRUCT_DECL,
            K.CLASS_TEMPLATE,
        }
        functions = {
            K.FUNCTION_DECL,
            K.CXX_METHOD,
            K.CONSTRUCTOR,
            K.DESTRUCTOR,
            K.FUNCTION_TEMPLATE,
            K.CONVERSION_FUNCTION,
        }
        for child in cursor.get_children():
            loc_file = child.location.file
            if loc_file is None:
                continue
            rel = self._rel(loc_file.name)
            if rel is None:
                continue
            kind = child.kind
            if kind in containers:
                self._visit(child)
            elif kind in functions and child.is_definition():
                self._function(child, rel)

    def _qualified_name(self, cursor: Any) -> str:
        K = self.ci.CursorKind
        name = str(cursor.spelling)
        parent = cursor.semantic_parent
        if parent is not None and parent.kind in (
            K.CLASS_DECL,
            K.STRUCT_DECL,
            K.CLASS_TEMPLATE,
        ):
            name = f"{parent.spelling}::{name}"
        return name

    def _function(self, cursor: Any, rel: str) -> None:
        name = self._qualified_name(cursor)
        start = cursor.extent.start.line
        end = cursor.extent.end.line
        key = (rel, name, start)
        if key in self.seen:
            return
        self.seen.add(key)
        try:
            is_static = cursor.storage_class == self.ci.StorageClass.STATIC
        except Exception:
            is_static = False
        analysis = self._result(rel)
        analysis.functions.append(
            RawFunction(name=name, start_line=start, end_line=end, is_static=is_static)
        )
        index = len(analysis.functions) - 1
        self._calls(cursor, analysis, index)
        text, lines = self._text(rel)
        body_start = lines.offset_of_line(start)
        body_end = lines.offset_of_line(end + 1)
        self.extractor.extract(
            text, lines, analysis, index, body_start, body_end, with_calls=False
        )

    def _calls(self, function: Any, analysis: RawFileAnalysis, index: int) -> None:
        K = self.ci.CursorKind
        callable_kinds = {
            K.FUNCTION_DECL,
            K.CXX_METHOD,
            K.CONSTRUCTOR,
            K.DESTRUCTOR,
            K.FUNCTION_TEMPLATE,
            K.CONVERSION_FUNCTION,
        }
        pointer_holders = {K.VAR_DECL, K.PARM_DECL, K.FIELD_DECL}
        async_names = self.rules.names
        seen: set[tuple[str, int, str]] = set()
        for node in function.walk_preorder():
            if node.kind != K.CALL_EXPR:
                continue
            line = node.location.line
            ref = node.referenced
            name: Optional[str] = None
            kind = CALL_DIRECT
            if ref is not None and ref.kind in callable_kinds:
                name = self._qualified_name(ref)
                if ref.kind == K.CONSTRUCTOR:
                    continue
            elif ref is None or ref.kind in pointer_holders:
                callee = self._callee_expr(node)
                if callee is not None:
                    kind, name = callee
                elif ref is not None:
                    name, kind = ref.spelling, CALL_POINTER
                elif node.spelling:
                    name = node.spelling
            if not name:
                continue
            if name in async_names or name.rsplit("::", 1)[-1] in async_names:
                continue
            key = (name, line, kind)
            if key in seen:
                continue
            seen.add(key)
            analysis.calls.append(RawCall(index, name, line, kind))

    def _callee_expr(self, call: Any) -> Optional[tuple[str, str]]:
        """呼び出し先の式（最初の子）から (kind, 名前) を求める。

        obj->field(...)  → (field, "field")
        (*fp)(...) / fp(...) → (pointer, "fp")
        """
        K = self.ci.CursorKind
        children = list(call.get_children())
        if not children:
            return None
        stack = [children[0]]
        steps = 0
        while stack and steps < 16:
            node = stack.pop(0)
            steps += 1
            if node.kind == K.MEMBER_REF_EXPR:
                return CALL_FIELD, str(node.spelling)
            if node.kind == K.DECL_REF_EXPR:
                ref = node.referenced
                if ref is not None and ref.kind == K.FUNCTION_DECL:
                    return None
                return CALL_POINTER, str(node.spelling)
            stack.extend(node.get_children())
        return None


# ---------------------------------------------------------------------------


def libclang_available() -> bool:
    try:
        import clang.cindex as ci

        ci.Index.create()
        return True
    except Exception:
        return False


class ClangSourceAnalyzer:
    """libclang を用いた SourceAnalyzer。"""

    name = "clang"

    def __init__(
        self,
        settings: Settings,
        work_dir: Path,
        all_files: Optional[Iterable[SourceFile]] = None,
    ) -> None:
        self._settings = settings
        self._workspace = ClangWorkspace(work_dir)
        self._all_files = all_files

    def analyze_files(self, files: Iterable[SourceFile]) -> Iterator[RawFileAnalysis]:
        targets = [f.path for f in files]
        # include 解決には除外ファイルも含めて展開する
        self._workspace.prepare(self._all_files if self._all_files is not None else [])
        jobs = self._jobs(targets)
        if not jobs:
            return
        cfg = self._settings.analyzer
        base_args = [
            "--target=x86_64-unknown-linux-gnu",
            "-nostdinc",
            "-ferror-limit=0",
            "-Wno-everything",
            "-include",
            str(self._workspace.prelude),
        ]
        base_args += [f"-D{d}" for d in cfg.defines]
        base_args += [f"-I{d}" for d in self._workspace.include_dirs]
        base_args.append(f"-I{self._workspace.stubs}")
        specs = [(s.name, s.kind, s.arg, s.handle_arg) for s in self._settings.async_apis]
        workers = cfg.jobs if cfg.jobs > 0 else max(1, (os.cpu_count() or 2) - 1)
        seen: set[tuple[str, str, int]] = set()
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(str(self._workspace.src), base_args, specs),
        ) as pool:
            for results in pool.map(_worker_parse, jobs, chunksize=4):
                for data in results:
                    analysis = RawFileAnalysis.from_dict(data)
                    yield from self._dedupe(analysis, seen)

    def _jobs(self, targets: list[str]) -> list[tuple[str, list[str]]]:
        cfg = self._settings.analyzer
        jobs: list[tuple[str, list[str]]] = []
        for rel in targets:
            lower = rel.lower()
            if lower.endswith(TU_EXTENSIONS_C):
                lang = ["-x", "c", *cfg.c_args]
            elif lower.endswith(TU_EXTENSIONS_CXX):
                lang = ["-x", "c++", *cfg.cxx_args]
            else:
                continue
            jobs.append((str(self._workspace.src / rel), lang))
        return jobs

    @staticmethod
    def _dedupe(
        analysis: RawFileAnalysis, seen: set[tuple[str, str, int]]
    ) -> Iterator[RawFileAnalysis]:
        """ヘッダ内の関数は複数の翻訳単位で現れるため重複を除く。"""
        keep: list[int] = []
        for i, fn in enumerate(analysis.functions):
            key = (analysis.path, fn.name, fn.start_line)
            if key in seen:
                continue
            seen.add(key)
            keep.append(i)
        if len(keep) == len(analysis.functions):
            yield analysis
            return
        remap = {old: new for new, old in enumerate(keep)}
        filtered = RawFileAnalysis(path=analysis.path, errors=analysis.errors)
        filtered.functions = [analysis.functions[i] for i in keep]
        filtered.calls = [c for c in analysis.calls if c.caller in remap]
        for c in filtered.calls:
            c.caller = remap[c.caller]
        filtered.async_calls = [a for a in analysis.async_calls if a.caller in remap]
        for a in filtered.async_calls:
            a.caller = remap[a.caller]
        filtered.refs = [
            r for r in analysis.refs if r.caller is None or r.caller in remap
        ]
        for r in filtered.refs:
            if r.caller is not None:
                r.caller = remap[r.caller]
        if filtered.functions or filtered.refs or filtered.errors:
            yield filtered
