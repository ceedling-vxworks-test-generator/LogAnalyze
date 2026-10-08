"""ログ解析（ストリーミング）。

    PhysicalLineReader  : 物理行を 1 行ずつ yield
    LogRecordAssembler  : 物理行 → 論理レコード（割り込み・分断の修復）
    LogParser           : 論理レコード → LogEntry

いずれもジェネレータで連結し、ファイル全体をメモリに載せない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

from ..domain.models import LogEntry, LogKind, LogLevel
from .log_grammar import LogGrammar, UtLogGrammar


@dataclass(frozen=True, slots=True)
class PhysicalLine:
    line_no: int
    timestamp: Optional[str]
    body: str


@dataclass(slots=True)
class LogicalRecord:
    """組立て中/組立て済みのレコード。"""

    line_no: int
    timestamp: Optional[str]
    body: str
    kind: LogKind
    deferred: list["LogicalRecord"] = field(default_factory=list)


class PhysicalLineReader:
    def __init__(
        self,
        grammar: LogGrammar,
        encoding: str = "utf-8",
        errors: str = "replace",
    ) -> None:
        self._grammar = grammar
        self._encoding = encoding
        self._errors = errors

    def read(self, path: Path) -> Iterator[PhysicalLine]:
        with Path(path).open(
            "r", encoding=self._encoding, errors=self._errors, newline=""
        ) as fp:
            yield from self.read_lines(fp)

    def read_lines(self, lines: Iterable[str]) -> Iterator[PhysicalLine]:
        for no, raw in enumerate(lines, start=1):
            text = raw.rstrip("\r\n")
            ts, body = self._grammar.split_timestamp(text)
            yield PhysicalLine(no, ts, body)


class LogRecordAssembler:
    """物理行から論理レコードを組み立てる。

    修復する現象:
    1. 別出力の割り込み  ``...(file.c:226 func*****X[Dispatch(51)]`` + 次行 ``)``
       → 割り込み部分を TRACE レコードとして切り出し、中断されたレコードの後に出す。
       → 途中で切れた末尾情報に後続行を連結する。
    2. 改行入りメッセージ ``...[func(58)]`` + 次行 `` (file.c:58 func)``
       → 末尾情報だけの行を直前レコードに連結する。
    """

    def __init__(self, grammar: LogGrammar) -> None:
        self._g = grammar

    def assemble(self, lines: Iterable[PhysicalLine]) -> Iterator[LogicalRecord]:
        pending: Optional[LogicalRecord] = None

        def flush() -> Iterator[LogicalRecord]:
            nonlocal pending
            if pending is not None:
                rec = pending
                pending = None
                yield rec
                yield from rec.deferred
                rec.deferred = []

        for line in lines:
            body, interrupts = self._g.extract_interrupts(line.body)
            interrupt_records = [
                LogicalRecord(line.line_no, line.timestamp, text, LogKind.TRACE)
                for text in interrupts
            ]

            consumed = False
            if pending is not None and pending.kind is LogKind.STRUCTURED:
                if self._g.find_trailer(pending.body) is None:
                    if self._g.has_partial_trailer(pending.body) and body.strip():
                        if self._g.match_head(body) is None:
                            pending.body += body.strip()
                            consumed = True
                    elif self._g.is_pure_trailer(body):
                        pending.body += "\n" + body.strip()
                        consumed = True

            if consumed:
                assert pending is not None
                pending.deferred.extend(interrupt_records)
                if self._g.find_trailer(pending.body) is not None:
                    yield from flush()
                continue

            yield from flush()

            if self._g.match_head(body) is not None:
                pending = LogicalRecord(
                    line.line_no, line.timestamp, body, LogKind.STRUCTURED
                )
                pending.deferred.extend(interrupt_records)
                continue

            if not body.strip():
                yield from interrupt_records
                continue

            kind = LogKind.TRACE if self._g.match_trace(body) else LogKind.RAW
            yield LogicalRecord(line.line_no, line.timestamp, body, kind)
            yield from interrupt_records

        yield from flush()


class LogParser:
    """ログファイルを LogEntry のジェネレータとして読む。"""

    def __init__(
        self,
        grammar: Optional[LogGrammar] = None,
        encoding: str = "utf-8",
        errors: str = "replace",
    ) -> None:
        self._grammar: LogGrammar = grammar if grammar is not None else UtLogGrammar()
        self._reader = PhysicalLineReader(self._grammar, encoding, errors)
        self._assembler = LogRecordAssembler(self._grammar)

    def parse(self, path: Path) -> Iterator[LogEntry]:
        return self.parse_physical(self._reader.read(path))

    def parse_lines(self, lines: Iterable[str]) -> Iterator[LogEntry]:
        """文字列の行（改行あり/なし）を解析する。テスト・パイプ入力用。"""
        return self.parse_physical(self._reader.read_lines(lines))

    def parse_physical(self, lines: Iterable[PhysicalLine]) -> Iterator[LogEntry]:
        last_text: Optional[str] = None
        cache_text: Optional[str] = None
        cache_val: tuple[Optional[datetime], Optional[int]] = (None, None)
        for seq, rec in enumerate(self._assembler.assemble(lines)):
            # タイムスタンプなし行は直前の時刻を引き継ぐ
            ts_text = rec.timestamp if rec.timestamp is not None else last_text
            if ts_text is not None and ts_text != cache_text:
                cache_text = ts_text
                cache_val = self._grammar.parse_timestamp(ts_text)
            dt, ms = cache_val if ts_text is not None else (None, None)
            last_text = ts_text
            yield self._to_entry(seq, rec, dt, ms)

    def _to_entry(
        self,
        seq: int,
        rec: LogicalRecord,
        timestamp: Optional[datetime],
        ms: Optional[int],
    ) -> LogEntry:
        g = self._grammar
        if rec.kind is LogKind.STRUCTURED:
            head = g.match_head(rec.body)
            if head is not None:
                rest = head.rest
                trailer = g.find_trailer(rest)
                if trailer is not None:
                    message = rest[: trailer.start].rstrip()
                    return LogEntry(
                        seq=seq,
                        line_no=rec.line_no,
                        timestamp=timestamp,
                        timestamp_ms=ms,
                        thread_id=None,
                        tick=head.tick,
                        log_level=LogLevel.parse(head.level),
                        module_name=head.module or None,
                        function_name=trailer.function_name,
                        message=message,
                        file_name=trailer.file_name,
                        line_number=trailer.line_number,
                        kind=LogKind.STRUCTURED,
                        raw=rec.body,
                    )
                return LogEntry(
                    seq=seq,
                    line_no=rec.line_no,
                    timestamp=timestamp,
                    timestamp_ms=ms,
                    thread_id=None,
                    tick=head.tick,
                    log_level=LogLevel.parse(head.level),
                    module_name=head.module or None,
                    function_name=None,
                    message=rest.rstrip(),
                    file_name=None,
                    line_number=None,
                    kind=LogKind.STRUCTURED,
                    raw=rec.body,
                )
        if rec.kind is LogKind.TRACE:
            trace = g.match_trace(rec.body)
            if trace is not None:
                return LogEntry(
                    seq=seq,
                    line_no=rec.line_no,
                    timestamp=timestamp,
                    timestamp_ms=ms,
                    thread_id=None,
                    tick=None,
                    log_level=LogLevel.UNKNOWN,
                    module_name=None,
                    function_name=trace.function_name,
                    message=trace.message,
                    file_name=None,
                    line_number=trace.line_number,
                    kind=LogKind.TRACE,
                    raw=rec.body,
                )
        return LogEntry(
            seq=seq,
            line_no=rec.line_no,
            timestamp=timestamp,
            timestamp_ms=ms,
            thread_id=None,
            tick=None,
            log_level=LogLevel.UNKNOWN,
            module_name=None,
            function_name=None,
            message=rec.body.strip(),
            file_name=None,
            line_number=None,
            kind=LogKind.RAW,
            raw=rec.body,
        )
