"""ソース解析器の共通出力（ファイル単位・名前ベース）。

解析器（clang / regex）はファイル単位で RawFileAnalysis を返し、
名前 → 関数 ID の解決は CallGraphBuilder が全ファイルを見て行う。
JSON に変換できる単純なデータのみで構成する（キャッシュ・プロセス間転送用）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

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
