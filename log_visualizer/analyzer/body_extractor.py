"""関数本体テキストからの抽出（呼び出し・非同期 API・関数参照）。

clang 解析器・正規表現解析器の両方から使う。
入力テキストは prepare_source() 済み（コメント・リテラル除去）であること。
"""

from __future__ import annotations

import re
from typing import Optional

from .async_rules import AsyncApiRules
from .raw_model import (
    CALL_DIRECT,
    CALL_FIELD,
    CALL_POINTER,
    RawCall,
    RawFileAnalysis,
    RawFunctionRef,
)
from .text_utils import LineIndex, find_matching, iter_arg_refs, iter_calls, iter_value_refs

_STD_THREAD_VAR = re.compile(r"\bstd\s*::\s*j?thread\s+[A-Za-z_]\w*\s*([({])")
# std::thread(fn) は iter_calls で拾えるので、ここでは波括弧初期化のみ
_STD_THREAD_TMP = re.compile(r"\bstd\s*::\s*j?thread\s*(\{)")


class BodyExtractor:
    def __init__(self, rules: AsyncApiRules) -> None:
        self._rules = rules

    def extract(
        self,
        text: str,
        lines: LineIndex,
        analysis: RawFileAnalysis,
        caller: int,
        start: int,
        end: int,
        with_calls: bool = True,
    ) -> None:
        """text[start:end]（関数本体）を解析し analysis に追記する。"""
        rules = self._rules
        params = analysis.functions[caller].params
        for name, paren, kind in iter_calls(text, start, end):
            line = lines.line_of(paren)
            close = find_matching(text, paren)
            args_text = text[paren + 1: close] if close > paren else ""
            spec = rules.spec_for(name)
            if spec is not None:
                call = rules.build_call_from_text(spec, args_text, caller, line)
                if call is not None:
                    analysis.async_calls.append(call)
                continue
            if with_calls:
                call_kind = {"direct": CALL_DIRECT, "field": CALL_FIELD}.get(
                    kind, CALL_POINTER
                )
                if call_kind == CALL_DIRECT and _is_param(name, params):
                    call_kind = CALL_POINTER
                analysis.calls.append(RawCall(caller, name, line, call_kind))
            for _, ref in iter_arg_refs(args_text):
                analysis.refs.append(
                    RawFunctionRef(caller, ref, line, None, via_call=name)
                )

        self._extract_std_thread(text, lines, analysis, caller, start, end)

        for name, pos, field_name in iter_value_refs(text, start, end):
            analysis.refs.append(
                RawFunctionRef(caller, name, lines.line_of(pos), field_name)
            )

    def _extract_std_thread(
        self,
        text: str,
        lines: LineIndex,
        analysis: RawFileAnalysis,
        caller: int,
        start: int,
        end: int,
    ) -> None:
        spec = self._rules.spec_for("std::thread")
        if spec is None:
            return
        for pattern in (_STD_THREAD_VAR, _STD_THREAD_TMP):
            for m in pattern.finditer(text, start, end):
                open_pos = m.start(1)
                open_ch = text[open_pos]
                close = find_matching(text, open_pos, open_ch, "}" if open_ch == "{" else ")")
                if close < 0:
                    continue
                call = self._rules.build_call_from_text(
                    spec, text[open_pos + 1: close], caller, lines.line_of(open_pos)
                )
                if call is not None:
                    analysis.async_calls.append(call)

    @staticmethod
    def file_scope_refs(
        text: str,
        lines: LineIndex,
        analysis: RawFileAnalysis,
        ranges: list[tuple[int, int]],
    ) -> None:
        """関数本体の外（初期化子など）にある関数参照を集める。"""
        cursor = 0
        spans: list[tuple[int, int]] = []
        for s, e in sorted(ranges):
            if s > cursor:
                spans.append((cursor, s))
            cursor = max(cursor, e)
        if cursor < len(text):
            spans.append((cursor, len(text)))
        for s, e in spans:
            for name, pos, field_name in iter_value_refs(text, s, e, positional=True):
                analysis.refs.append(
                    RawFunctionRef(None, name, lines.line_of(pos), field_name)
                )


def _is_param(name: str, params: str) -> bool:
    if not params:
        return False
    return re.search(r"\b" + re.escape(name) + r"\b", params) is not None


def extract_params(header: str) -> Optional[str]:
    """関数ヘッダ文字列から引数部分を取り出す。"""
    open_pos = header.find("(")
    if open_pos < 0:
        return None
    close = find_matching(header, open_pos)
    if close < 0:
        return None
    return header[open_pos + 1: close]
