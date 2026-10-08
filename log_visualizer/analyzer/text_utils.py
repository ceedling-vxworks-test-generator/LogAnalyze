"""C/C++ ソースのテキスト処理ユーティリティ（解析器共通）。"""

from __future__ import annotations

import bisect
import re
from typing import Iterator, Optional

_COMMENT_OR_LITERAL = re.compile(
    r"//[^\n]*"
    r"|/\*.*?\*/"
    r"|\"(?:\\.|[^\"\\\n])*\""
    r"|'(?:\\.|[^'\\\n])*'",
    re.DOTALL,
)

C_KEYWORDS = frozenset(
    {
        "if", "else", "for", "while", "do", "switch", "case", "return", "sizeof",
        "goto", "break", "continue", "default", "typedef", "struct", "union", "enum",
        "static", "extern", "const", "volatile", "register", "inline", "auto",
        "_Alignof", "alignof", "_Generic", "_Static_assert", "static_assert",
        "defined", "typeof", "__typeof__", "__typeof", "decltype", "catch", "throw",
        "new", "delete", "operator", "template", "typename", "using", "namespace",
        "class", "public", "private", "protected", "try", "noexcept", "__attribute__",
        "__attribute", "__declspec", "__asm__", "asm", "__asm", "alignas", "_Alignas",
        "static_cast", "dynamic_cast", "reinterpret_cast", "const_cast", "va_arg",
        "offsetof", "__builtin_offsetof", "__builtin_va_arg",
    }
)


def strip_comments_and_literals(text: str) -> str:
    """コメントと文字列/文字リテラルを空白に置換する（改行・桁位置は保持）。"""

    def repl(m: re.Match[str]) -> str:
        s = m.group(0)
        if s.startswith("/"):
            return "".join("\n" if ch == "\n" else " " for ch in s)
        quote = s[0]
        return quote + " " * (len(s) - 2) + quote

    return _COMMENT_OR_LITERAL.sub(repl, text)


_DIRECTIVE = re.compile(r"^[ \t]*#[ \t]*(\w+)(.*)$")


def blank_preprocessor(text: str) -> str:
    """プリプロセッサ指令を空白化し、#if 系は 1 つの分岐だけを残す。

    - #if 0 の分岐は捨てて #else 側を残す
    - それ以外は最初の分岐を残し、#elif / #else 側を捨てる
    波括弧の対応を崩さないための近似処理。
    """
    lines = text.split("\n")
    out: list[str] = []
    # 各要素: (このレベルを出力中か, 既にどこかの分岐を採用したか)
    stack: list[tuple[bool, bool]] = []
    continuation = False

    def active() -> bool:
        return all(state[0] for state in stack)

    for line in lines:
        if continuation:
            continuation = line.rstrip().endswith("\\")
            out.append("")
            continue
        m = _DIRECTIVE.match(line)
        if m is None:
            out.append(line if active() else "")
            continue
        continuation = line.rstrip().endswith("\\")
        directive = m.group(1)
        arg = m.group(2).strip()
        if directive in ("if", "ifdef", "ifndef"):
            is_zero = directive == "if" and arg in ("0", "(0)", "false")
            stack.append((not is_zero, not is_zero))
        elif directive == "elif":
            if stack:
                _, taken = stack[-1]
                if taken:
                    stack[-1] = (False, True)
                else:
                    use = arg not in ("0", "(0)", "false")
                    stack[-1] = (use, use)
        elif directive == "else":
            if stack:
                _, taken = stack[-1]
                stack[-1] = (not taken, True)
        elif directive == "endif":
            if stack:
                stack.pop()
        out.append("")
    return "\n".join(out)


def prepare_source(text: str) -> str:
    """コメント・リテラル除去 → プリプロセッサ処理。"""
    return blank_preprocessor(strip_comments_and_literals(text))


class LineIndex:
    """文字オフセット → 行番号（1 起点）。"""

    def __init__(self, text: str) -> None:
        self._starts = [0]
        self._starts.extend(m.end() for m in re.finditer(r"\n", text))

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self._starts, offset)

    def offset_of_line(self, line: int) -> int:
        if line <= 1:
            return 0
        if line - 1 >= len(self._starts):
            return self._starts[-1]
        return self._starts[line - 1]


def find_matching(text: str, open_pos: int, open_ch: str = "(", close_ch: str = ")") -> int:
    """open_pos の括弧に対応する閉じ括弧位置。見つからなければ -1。"""
    depth = 0
    for i in range(open_pos, len(text)):
        ch = text[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return -1


def split_args(args: str) -> list[str]:
    """トップレベルのカンマで引数を分割する。"""
    result: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in args:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            result.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail or result:
        result.append(tail)
    return result


_CAST = re.compile(r"^\(\s*[A-Za-z_][\w\s\*]*\)\s*")
_IDENT = re.compile(r"[A-Za-z_]\w*")


def normalize_key(expr: str) -> Optional[str]:
    """キュー/ハンドル式の正規化: 末尾のメンバ名/変数名。"""
    text = expr.strip()
    while True:
        m = _CAST.match(text)
        if m is None:
            break
        text = text[m.end():]
    text = text.lstrip("&*( ").rstrip(") ")
    text = re.sub(r"\[[^\]]*\]", "", text)
    idents: list[str] = _IDENT.findall(text)
    if not idents:
        return None
    return idents[-1]


def function_name_of(expr: str) -> Optional[str]:
    """関数参照式（&Class::method, (FUNC)fn など）から関数名を取り出す。"""
    text = expr.strip()
    while True:
        m = _CAST.match(text)
        if m is None:
            break
        text = text[m.end():]
    text = text.lstrip("&( ").rstrip(") ")
    m = re.fullmatch(r"((?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*)", text)
    if m is None:
        return None
    name = re.sub(r"\s+", "", m.group(1))
    if name in C_KEYWORDS or name in ("NULL", "nullptr", "this", "true", "false"):
        return None
    return name


def iter_calls(text: str, start: int, end: int) -> Iterator[tuple[str, int, str]]:
    """text[start:end] の呼び出し (name, 開き括弧位置, kind) を列挙する。"""
    for m in _CALL.finditer(text, start, end):
        paren = m.end() - 1
        deref = m.group("deref")
        if deref:
            # (*fp)(...) / (*ctx->fp)(...) : 最後の識別子を名前とする
            last = _IDENT.findall(deref)[-1]
            yield last, paren, "field" if ("->" in deref or "." in deref) else "pointer"
            continue
        prefix = m.group("prefix")
        name = re.sub(r"\s+", "", m.group("name"))
        base = name.rsplit("::", 1)[-1]
        if base in C_KEYWORDS:
            continue
        if prefix and prefix.strip() in ("->", "."):
            yield name, paren, "field"
        else:
            yield name, paren, "direct"


_CALL = re.compile(
    r"(?:\(\s*\*\s*(?P<deref>[A-Za-z_][\w.\->\[\]]*?)\s*\)\s*\()"
    r"|(?P<prefix>->\s*|\.\s*|(?<![\w.>]))"
    r"(?P<name>(?:[A-Za-z_]\w*\s*::\s*)*~?[A-Za-z_]\w*)\s*(?:<[^<>;(){}]*>\s*)?\("
)

_FIELD_ASSIGN = re.compile(
    r"(?:\.|->)\s*(?P<field>[A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)?=(?!=)\s*&?\s*"
    r"(?:\(\s*[A-Za-z_][\w\s\*]*\)\s*)?(?P<fn>[A-Za-z_]\w*(?:\s*::\s*[A-Za-z_]\w*)*)\s*(?=[;,}\)])"
)
_PLAIN_ASSIGN = re.compile(
    r"(?<![=!<>+\-*/%&|^])=(?!=)\s*&?\s*(?:\(\s*[A-Za-z_][\w\s\*]*\)\s*)?"
    r"(?P<fn>[A-Za-z_]\w*(?:\s*::\s*[A-Za-z_]\w*)*)\s*(?=[;,}])"
)
_POSITIONAL_INIT = re.compile(
    r"(?<=[{,])\s*&?\s*(?P<fn>[A-Za-z_]\w*)\s*(?=[,}])"
)


def iter_value_refs(
    text: str, start: int, end: int, positional: bool = False
) -> Iterator[tuple[str, int, Optional[str]]]:
    """関数を値として使っていそうな識別子 (name, 位置, field) を列挙する。

    positional=True のときは位置指定の初期化子 ``{ fn_a, fn_b }`` も対象にする
    （関数本体内では呼び出し引数と区別できないため、ファイルスコープでのみ使う）。
    実在する関数名かどうかは CallGraphBuilder が判定する。
    """
    for m in _FIELD_ASSIGN.finditer(text, start, end):
        yield re.sub(r"\s+", "", m.group("fn")), m.start("fn"), m.group("field")
    for m in _PLAIN_ASSIGN.finditer(text, start, end):
        yield re.sub(r"\s+", "", m.group("fn")), m.start("fn"), None
    if positional:
        for m in _POSITIONAL_INIT.finditer(text, start, end):
            yield m.group("fn"), m.start("fn"), None


def iter_arg_refs(args_text: str) -> Iterator[tuple[int, str]]:
    """引数リスト文字列から、関数参照になり得る引数 (位置, 名前) を列挙する。"""
    for index, arg in enumerate(split_args(args_text)):
        name = function_name_of(arg)
        if name is not None:
            yield index, name
