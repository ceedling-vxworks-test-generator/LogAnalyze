"""関数 ID の採番。

ID = "<パス末尾>::<関数名>"。パス末尾は通常ファイル名だけだが、
同名ファイルが複数ある場合は重複しなくなるまで親ディレクトリを足す。

    ingress_task1.c::handle_submit
    Config/FaultManager_Config.c::FaultManager_OnFaultListChanged
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable


def unique_suffixes(paths: Iterable[str]) -> dict[str, str]:
    """各パスについて、他と重複しない最短のパス末尾を返す。"""
    unique = sorted(set(paths))
    parts = {p: p.split("/") for p in unique}
    depth = {p: 1 for p in unique}
    while True:
        groups: dict[str, list[str]] = defaultdict(list)
        for p in unique:
            groups["/".join(parts[p][-depth[p]:])].append(p)
        changed = False
        for members in groups.values():
            if len(members) <= 1:
                continue
            for p in members:
                if depth[p] < len(parts[p]):
                    depth[p] += 1
                    changed = True
        if not changed:
            break
    return {p: "/".join(parts[p][-depth[p]:]) for p in unique}


def make_function_id(suffix: str, name: str) -> str:
    return f"{suffix}::{name}"


def split_function_id(function_id: str) -> tuple[str, str]:
    """ID をパス末尾と関数名に分ける（関数名側に :: を含み得る）。"""
    marker = function_id.find("::")
    if marker < 0:
        return "", function_id
    # パス末尾には "." が含まれる。最初の "." 以降の最初の "::" で区切る
    dot = function_id.find(".")
    if dot >= 0:
        sep = function_id.find("::", dot)
        if sep >= 0:
            return function_id[:sep], function_id[sep + 2:]
    return function_id[:marker], function_id[marker + 2:]
