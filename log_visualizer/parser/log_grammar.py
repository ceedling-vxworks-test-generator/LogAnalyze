"""ログ書式（文法）定義。

既定は utility_log_console.c の出力書式:

    [YYYY-MM-DD hh:mm:ss.mmm] [%llu] %-5s %-23s %s (%s:%u %s)

別書式に対応する場合は LogGrammar Protocol を満たすクラスを作り LogParser に渡す。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

LEVEL_NAMES = ("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL", "UNKNOWN")

_EPOCH = datetime(1970, 1, 1)


@dataclass(frozen=True, slots=True)
class StructuredHead:
    """記録開始行の先頭部分。"""

    tick: int
    level: str
    module: str
    rest: str  # メッセージ（＋末尾情報）


@dataclass(frozen=True, slots=True)
class Trailer:
    """末尾の "(file:line func)"。"""

    file_name: str
    line_number: int
    function_name: str
    start: int  # 本文中の開始位置（直前の空白を含む）


@dataclass(frozen=True, slots=True)
class TraceMatch:
    """*****...[func(line)] 形式。"""

    message: str
    function_name: str
    line_number: int


class LogGrammar(Protocol):
    def split_timestamp(self, line: str) -> tuple[Optional[str], str]:
        ...

    def parse_timestamp(self, text: str) -> tuple[Optional[datetime], Optional[int]]:
        ...

    def match_head(self, body: str) -> Optional[StructuredHead]:
        ...

    def find_trailer(self, text: str) -> Optional[Trailer]:
        ...

    def has_partial_trailer(self, text: str) -> bool:
        ...

    def is_pure_trailer(self, body: str) -> bool:
        ...

    def match_trace(self, body: str) -> Optional[TraceMatch]:
        ...

    def extract_interrupts(self, body: str) -> tuple[str, list[str]]:
        ...


class UtLogGrammar:
    """utility_log 形式の文法。"""

    MODULE_WIDTH = 23

    _TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?)\] ?")
    _HEAD = re.compile(r"^\[(\d+)\] (TRACE|DEBUG|INFO|WARN|ERROR|FATAL|UNKNOWN) ?")
    _FILE = r"[^\s():]+\.[A-Za-z0-9]{1,4}"
    _FUNC = r"[A-Za-z_~][\w:~<>]*"
    _TRAILER = re.compile(
        r"\s\((?P<file>" + _FILE + r"):(?P<line>\d+) (?P<func>" + _FUNC + r")\)\s*$"
    )
    _PARTIAL = re.compile(
        r"\s\((?:" + _FILE + r")(?::\d*(?: [\w:~<>]*)?)?$"
    )
    _PURE_TRAILER = re.compile(
        r"^\s*\((?P<file>" + _FILE + r"):(?P<line>\d+) (?P<func>" + _FUNC + r")\)\s*$"
    )
    _TRACE = re.compile(
        r"^\*{5,}(?P<msg>.*?)\s*\[(?P<func>[A-Za-z_]\w*)\((?P<line>\d+)\)\]\s*$"
    )
    # 単語の直後に始まる ***** は別出力の割り込みとみなす
    _INTERRUPT = re.compile(r"(?<=[\w)\]])\*{5,}.*?\[[A-Za-z_]\w*\(\d+\)\]")

    def split_timestamp(self, line: str) -> tuple[Optional[str], str]:
        m = self._TS.match(line)
        if m is None:
            return None, line
        return m.group(1), line[m.end():]

    def parse_timestamp(self, text: str) -> tuple[Optional[datetime], Optional[int]]:
        try:
            year = int(text[0:4])
            month = int(text[5:7])
            day = int(text[8:10])
            hour = int(text[11:13])
            minute = int(text[14:16])
            second = int(text[17:19])
            frac = text[20:] if len(text) > 20 else ""
            micro = int((frac + "000000")[:6]) if frac else 0
            dt = datetime(year, month, day, hour, minute, second, micro)
        except ValueError:
            return None, None
        delta = dt - _EPOCH
        ms = (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
        return dt, ms

    def match_head(self, body: str) -> Optional[StructuredHead]:
        m = self._HEAD.match(body)
        if m is None:
            return None
        tick = int(m.group(1))
        level = m.group(2)
        after = body[m.end():]
        # %-5s の残りパディング
        after = after.lstrip(" ") if len(level) < 5 else after
        width = self.MODULE_WIDTH
        if len(after) > width and after[width] == " " and after[:width].strip():
            module = after[:width].rstrip()
            rest = after[width + 1:]
        elif len(after) <= width:
            module = after.rstrip()
            rest = ""
        else:
            # 23 文字を超えるモジュール名: 最初の空白まで
            parts = after.split(None, 1)
            module = parts[0] if parts else ""
            rest = parts[1] if len(parts) > 1 else ""
        return StructuredHead(tick=tick, level=level, module=module, rest=rest)

    def find_trailer(self, text: str) -> Optional[Trailer]:
        m = self._TRAILER.search(text)
        if m is None:
            return None
        return Trailer(
            file_name=m.group("file"),
            line_number=int(m.group("line")),
            function_name=m.group("func"),
            start=m.start(),
        )

    def has_partial_trailer(self, text: str) -> bool:
        return self._PARTIAL.search(text) is not None

    def is_pure_trailer(self, body: str) -> bool:
        return self._PURE_TRAILER.match(body) is not None

    def match_trace(self, body: str) -> Optional[TraceMatch]:
        m = self._TRACE.match(body.strip())
        if m is None:
            return None
        return TraceMatch(
            message=m.group("msg").strip(),
            function_name=m.group("func"),
            line_number=int(m.group("line")),
        )

    def extract_interrupts(self, body: str) -> tuple[str, list[str]]:
        found = self._INTERRUPT.findall(body)
        if not found:
            return body, []
        return self._INTERRUPT.sub("", body), found
