"""ソース解析器の共通出力（ファイル単位・名前ベース）。

解析器（clang / regex）はファイル単位で RawFileAnalysis を返し、
名前 → 関数 ID の解決は CallGraphBuilder が全ファイルを見て行う。
JSON に変換できる単純なデータのみで構成する（キャッシュ・プロセス間転送用）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

# RawCall.kind
CALL_DIRECT = "direct"  # foo(...)
CALL_FIELD = "field"  # obj->foo(...) / obj.foo(...)（C: 関数ポインタ / C++: メソッド）
CALL_POINTER = "pointer"  # (*fp)(...) / 引数の関数ポインタ fp(...)


@dataclass(slots=True)
class RawFunction:
    name: str  # C++ は "Class::method"
    start_line: int
    end_line: int
    is_static: bool
    params: str = ""


@dataclass(slots=True)
class RawCall:
    caller: int  # RawFileAnalysis.functions の添字
    name: str
    line: int
    kind: str = CALL_DIRECT


@dataclass(slots=True)
class RawAsyncCall:
    caller: int
    api: str
    kind: str  # settings.ASYNC_KINDS
    line: int
    target: Optional[str] = None  # thread / callback_register のエントリ関数名
    key: Optional[str] = None  # queue / event のキー
    handle: Optional[str] = None  # thread のタスクハンドル


@dataclass(slots=True)
class RawFunctionRef:
    """関数を値として参照している箇所（コールバック登録候補）。"""

    caller: Optional[int]  # None = ファイルスコープ（初期化子など）
    name: str
    line: int
    field_name: Optional[str] = None  # ".on_done = fn" / "x->on_done = fn"
    via_call: Optional[str] = None  # register_cb(fn) の register_cb


@dataclass(slots=True)
class RawFileAnalysis:
    path: str
    functions: list[RawFunction] = field(default_factory=list)
    calls: list[RawCall] = field(default_factory=list)
    async_calls: list[RawAsyncCall] = field(default_factory=list)
    refs: list[RawFunctionRef] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RawFileAnalysis":
        return cls(
            path=data["path"],
            functions=[RawFunction(**f) for f in data.get("functions", [])],
            calls=[RawCall(**c) for c in data.get("calls", [])],
            async_calls=[RawAsyncCall(**a) for a in data.get("async_calls", [])],
            refs=[RawFunctionRef(**r) for r in data.get("refs", [])],
            errors=list(data.get("errors", [])),
        )


def merge_analyses(items: Iterable[RawFileAnalysis]) -> list[RawFileAnalysis]:
    """同じパスの解析結果を 1 つにまとめる。

    clang ではヘッダ内の関数が複数の翻訳単位から報告されるため、
    (関数名, 開始行) が同じ関数は 1 つにし、呼び出し等の添字を付け替える。
    """
    merged: dict[str, RawFileAnalysis] = {}
    keys: dict[str, dict[tuple[str, int], int]] = {}
    for item in items:
        target = merged.get(item.path)
        if target is None:
            target = RawFileAnalysis(path=item.path)
            merged[item.path] = target
            keys[item.path] = {}
        index_of = keys[item.path]
        remap: dict[int, int] = {}
        new_functions: set[int] = set()
        for old, fn in enumerate(item.functions):
            key = (fn.name, fn.start_line)
            idx = index_of.get(key)
            if idx is None:
                idx = len(target.functions)
                index_of[key] = idx
                target.functions.append(fn)
                new_functions.add(idx)
            remap[old] = idx
        # 既出の関数（別の翻訳単位で報告済み）の呼び出し等は重複になるので取り込まない
        for c in item.calls:
            if remap.get(c.caller) in new_functions:
                target.calls.append(RawCall(remap[c.caller], c.name, c.line, c.kind))
        for a in item.async_calls:
            if remap.get(a.caller) in new_functions:
                target.async_calls.append(
                    RawAsyncCall(remap[a.caller], a.api, a.kind, a.line, a.target, a.key, a.handle)
                )
        for r in item.refs:
            if r.caller is None:
                target.refs.append(r)
            elif remap.get(r.caller) in new_functions:
                target.refs.append(
                    RawFunctionRef(remap[r.caller], r.name, r.line, r.field_name, r.via_call)
                )
        target.errors.extend(item.errors)
    return list(merged.values())
