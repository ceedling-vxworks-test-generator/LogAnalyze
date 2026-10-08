"""正規表現ベースの簡易 C/C++ 解析器（libclang が使えない環境向け）。

手順:
1. コメント・文字列除去、#if 系は 1 分岐のみ残す
2. `{` `}` `;` を走査し、ブロックの直前テキスト（ヘッダ）で種別を判定
   - namespace / extern "C" / class / struct : 透過（中を引き続き走査）
   - 関数ヘッダ                               : 関数本体
   - それ以外（初期化子・enum 等）            : スキップ
3. 関数本体から呼び出し・非同期 API・関数参照を抽出
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

from ..config.settings import Settings
from ..domain.models import SourceFile
from .async_rules import AsyncApiRules
from .body_extractor import BodyExtractor, extract_params
from .raw_model import RawFileAnalysis, RawFunction
from .text_utils import C_KEYWORDS, LineIndex, find_matching, prepare_source

SOURCE_EXTENSIONS = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl")

_BOUNDARY = re.compile(r"[{};]")
_NAMESPACE = re.compile(r"(?:^|\s)(?:inline\s+)?namespace(?:\s+(?P<name>[\w:]+))?\s*$")
_EXTERN_C = re.compile(r"extern\s*\"[^\"]*\"\s*$")
_CLASS = re.compile(
    r"(?:^|[\s;])(?:typedef\s+)?(?P<kw>class|struct|union)\s+"
    r"(?:__attribute__\s*\(\(.*?\)\)\s*|alignas\s*\([^)]*\)\s*|[A-Z_]+_API\s+)*"
    r"(?P<name>[A-Za-z_]\w*)?\s*(?:final\s*)?(?::[^{;]*)?$"
)
_ANON_AGGREGATE = re.compile(r"(?:^|\s)(?:typedef\s+)?(?:struct|union|enum)\s*$")
_ENUM = re.compile(r"(?:^|\s)enum\b")
_ATTRIBUTE_NAMES = frozenset(
    {"__attribute__", "__attribute", "__declspec", "alignas", "_Alignas", "__asm__"}
)
_TRAILING_QUALIFIERS = re.compile(
    r"^\s*(?:(?:const|volatile|noexcept(?:\s*\([^)]*\))?|override|final|&&|&"
    r"|throw\s*\([^)]*\)|->\s*[\w:<>,\s\*&]+?|__attribute__\s*\(\(.*?\)\))\s*)*"
    r"(?::(?P<init>.*))?$",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class _Header:
    name: str
    params: str
    is_static: bool
    name_offset: int  # ヘッダ内での関数名の位置


def parse_function_header(header: str) -> Optional[_Header]:
    """ブロック直前のテキストが関数定義ヘッダなら情報を返す。"""
    if "=" in header.replace("==", "").replace("!=", "").replace("<=", "").replace(
        ">=", ""
    ).split("(", 1)[0]:
        return None  # 代入式・初期化子
    pos = 0
    while True:
        open_pos = header.find("(", pos)
        if open_pos < 0:
            return None
        m = re.search(r"((?:[A-Za-z_]\w*\s*::\s*)*~?[A-Za-z_]\w*|operator\s*\S+?)\s*$", header[:open_pos])
        close = find_matching(header, open_pos)
        if close < 0:
            return None
        if m is None:
            return None
        name = re.sub(r"\s+", "", m.group(1))
        base = name.rsplit("::", 1)[-1]
        if base in _ATTRIBUTE_NAMES:
            pos = close + 1
            continue
        if base in C_KEYWORDS and not name.startswith("operator"):
            return None
        rest = header[close + 1:]
        q = _TRAILING_QUALIFIERS.match(rest)
        if q is None:
            return None
        init = q.group("init")
        if init is not None and init.strip() and "(" not in init and "{" not in init:
            # "a ? b : c" 等を除外（コンストラクタ初期化子には括弧がある）
            return None
        prefix = header[: m.start(1)]
        if re.search(r"[=\[\]]", prefix):
            return None
        is_static = re.search(r"\bstatic\b", prefix) is not None
        return _Header(name, header[open_pos + 1: close], is_static, m.start(1))


class RegexFileParser:
    """1 ファイルを解析する。"""

    def __init__(self, rules: AsyncApiRules) -> None:
        self._extractor = BodyExtractor(rules)

    def analyze(self, source: SourceFile) -> RawFileAnalysis:
        text = prepare_source(source.content)
        lines = LineIndex(text)
        analysis = RawFileAnalysis(path=source.path)
        bodies: list[tuple[int, int]] = []
        for name, params, is_static, name_pos, body_start, body_end in self._iter_functions(
            text
        ):
            analysis.functions.append(
                RawFunction(
                    name=name,
                    start_line=lines.line_of(name_pos),
                    end_line=lines.line_of(body_end),
                    is_static=is_static,
                    params=" ".join(params.split()),
                )
            )
            index = len(analysis.functions) - 1
            self._extractor.extract(text, lines, analysis, index, body_start, body_end)
            bodies.append((body_start, body_end))
        BodyExtractor.file_scope_refs(text, lines, analysis, bodies)
        return analysis

    def _iter_functions(
        self, text: str
    ) -> Iterator[tuple[str, str, bool, int, int, int]]:
        # スタック要素: (種別, クラス名)
        stack: list[tuple[str, Optional[str]]] = []
        last_boundary = 0
        pos = 0
        length = len(text)
        while pos < length:
            m = _BOUNDARY.search(text, pos)
            if m is None:
                break
            ch = m.group(0)
            at = m.start()
            if ch == ";":
                last_boundary = at + 1
                pos = at + 1
                continue
            if ch == "}":
                if stack:
                    stack.pop()
                last_boundary = at + 1
                pos = at + 1
                continue
            # "{"
            header_raw = text[last_boundary:at]
            header = " ".join(header_raw.split())
            header = re.sub(r"^(?:public|private|protected)\s*:\s*", "", header)
            if _NAMESPACE.search(header) or _EXTERN_C.search(header):
                stack.append(("ns", None))
                last_boundary = at + 1
                pos = at + 1
                continue
            cls = _CLASS.search(header)
            if cls is not None and "(" not in header:
                stack.append(("class", cls.group("name")))
                last_boundary = at + 1
                pos = at + 1
                continue
            parsed = None
            if not _ENUM.search(header) and not _ANON_AGGREGATE.search(header):
                parsed = parse_function_header(header)
            close = find_matching(text, at, "{", "}")
            if parsed is None or close < 0:
                # 初期化子・enum 等: ブロックごと読み飛ばす
                end = close if close >= 0 else length - 1
                last_boundary = end + 1
                pos = end + 1
                continue
            name = parsed.name
            class_names = [n for kind, n in stack if kind == "class" and n]
            if class_names and "::" not in name:
                name = "::".join(class_names) + "::" + name
            # 関数名の位置（元テキスト上）
            name_pos = _locate_name(text, last_boundary, at, parsed.name)
            params = extract_params(header[parsed.name_offset:]) or parsed.params
            yield name, params, parsed.is_static, name_pos, at, close
            last_boundary = close + 1
            pos = close + 1


def _locate_name(text: str, start: int, end: int, name: str) -> int:
    base = name.rsplit("::", 1)[-1]
    idx = text.rfind(base, start, end)
    return idx if idx >= 0 else end


class RegexSourceAnalyzer:
    """正規表現ベースの SourceAnalyzer。"""

    name = "regex"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._parser = RegexFileParser(AsyncApiRules(settings.async_apis))

    def analyze_files(self, files: Iterable[SourceFile]) -> Iterator[RawFileAnalysis]:
        for source in files:
            if not source.path.lower().endswith(SOURCE_EXTENSIONS):
                continue
            yield self._parser.analyze(source)
