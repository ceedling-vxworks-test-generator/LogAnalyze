"""入力ファイル・フォルダをエクスプローラーのダイアログで選択する。

python -m log_visualizer を引数なし（または --gui 付き）で実行すると使われる。
前回選択した場所は ~/.log_visualizer/last_selection.json に保存し、次回の初期位置にする。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

STATE_PATH = Path.home() / ".log_visualizer" / "last_selection.json"


@dataclass(frozen=True)
class Selection:
    log: Path
    source: Optional[Path]
    out: Path


class Dialogs(Protocol):
    """ダイアログの抽象（テストで差し替えるため）。キャンセル時は空文字を返す。"""

    def open_file(self, title: str, initial_dir: str, filetypes: list[tuple[str, str]]) -> str:
        ...

    def directory(self, title: str, initial_dir: str) -> str:
        ...

    def yes_no(self, title: str, message: str) -> bool:
        ...

    def info(self, title: str, message: str) -> None:
        ...

    def error(self, title: str, message: str) -> None:
        ...


class TkDialogs:
    """tkinter（標準ライブラリ）によるダイアログ。"""

    def __init__(self) -> None:
        import tkinter

        self._root = tkinter.Tk()
        self._root.withdraw()
        self._root.attributes("-topmost", True)

    def open_file(self, title: str, initial_dir: str, filetypes: list[tuple[str, str]]) -> str:
        from tkinter import filedialog

        return str(filedialog.askopenfilename(
            parent=self._root, title=title, initialdir=initial_dir or None, filetypes=filetypes
        ) or "")

    def directory(self, title: str, initial_dir: str) -> str:
        from tkinter import filedialog

        return str(filedialog.askdirectory(
            parent=self._root, title=title, initialdir=initial_dir or None, mustexist=True
        ) or "")

    def yes_no(self, title: str, message: str) -> bool:
        from tkinter import messagebox

        return bool(messagebox.askyesno(title, message, parent=self._root))

    def info(self, title: str, message: str) -> None:
        from tkinter import messagebox

        messagebox.showinfo(title, message, parent=self._root)

    def error(self, title: str, message: str) -> None:
        from tkinter import messagebox

        messagebox.showerror(title, message, parent=self._root)


def _load_state(path: Path) -> dict[str, str]:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _save_state(path: Path, state: dict[str, str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # 保存できなくても動作には影響しない


def select_inputs(
    dialogs: Dialogs,
    log: Optional[Path] = None,
    source: Optional[Path] = None,
    out: Optional[Path] = None,
    state_path: Path = STATE_PATH,
) -> Optional[Selection]:
    """未指定の項目だけダイアログで選ばせる。ログが選ばれなければ None。"""
    state = _load_state(state_path)

    if log is None:
        chosen = dialogs.open_file(
            "ログファイルを選択",
            state.get("log_dir", ""),
            [("ログファイル", "*.log *.txt"), ("すべてのファイル", "*.*")],
        )
        if not chosen:
            return None
        log = Path(chosen)
    state["log_dir"] = str(log.parent)

    if source is None:
        chosen = dialogs.directory(
            "ソースコードのフォルダを選択（サブフォルダも再帰的に解析します）",
            state.get("source_dir", ""),
        )
        if chosen:
            source = Path(chosen)
            state["source_dir"] = chosen
        elif dialogs.yes_no(
            "ソースの指定",
            "ソースフォルダが選択されませんでした。\n\n"
            "ソースダンプ（1 ファイルにまとめた .txt）を選択しますか？\n"
            "「いいえ」を選ぶとログだけで可視化します（呼び出し矢印なし）。",
        ):
            chosen = dialogs.open_file(
                "ソースダンプを選択",
                state.get("dump_dir", state.get("log_dir", "")),
                [("テキスト", "*.txt"), ("すべてのファイル", "*.*")],
            )
            if chosen:
                source = Path(chosen)
                state["dump_dir"] = str(source.parent)

    if out is None:
        default_out = log.parent / "lv_output"
        chosen = dialogs.directory(
            f"出力先フォルダを選択（キャンセルで {default_out}）",
            state.get("out_dir", str(log.parent)),
        )
        out = Path(chosen) if chosen else default_out
        if chosen:
            state["out_dir"] = chosen

    _save_state(state_path, state)
    return Selection(log=log, source=source, out=out)
