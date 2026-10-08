"""非同期 API ルール（設定ファイル駆動）。"""

from __future__ import annotations

from typing import Iterable, Optional

from ..config.settings import AsyncApiSpec
from .raw_model import RawAsyncCall
from .text_utils import function_name_of, normalize_key, split_args


class AsyncApiRules:
    def __init__(self, specs: Iterable[AsyncApiSpec]) -> None:
        self._specs: dict[str, AsyncApiSpec] = {spec.name: spec for spec in specs}

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._specs)

    def spec_for(self, name: str) -> Optional[AsyncApiSpec]:
        return self._specs.get(name)

    def build_call(
        self, spec: AsyncApiSpec, args: list[str], caller: int, line: int
    ) -> Optional[RawAsyncCall]:
        """引数文字列から RawAsyncCall を作る。必要な引数がなければ None。"""
        arg = args[spec.arg] if spec.arg < len(args) else None
        if arg is None:
            return None
        if spec.kind in ("thread", "callback_register"):
            target = function_name_of(arg)
            if target is None:
                return None
            handle = None
            if spec.handle_arg is not None and spec.handle_arg < len(args):
                handle = normalize_key(args[spec.handle_arg])
            return RawAsyncCall(
                caller=caller,
                api=spec.name,
                kind=spec.kind,
                line=line,
                target=target,
                handle=handle,
            )
        key = normalize_key(arg)
        if key is None:
            return None
        return RawAsyncCall(
            caller=caller, api=spec.name, kind=spec.kind, line=line, key=key
        )

    def build_call_from_text(
        self, spec: AsyncApiSpec, args_text: str, caller: int, line: int
    ) -> Optional[RawAsyncCall]:
        return self.build_call(spec, split_args(args_text), caller, line)
