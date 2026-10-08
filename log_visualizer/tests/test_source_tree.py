"""ソースフォルダの再帰読込み・差分キャッシュ・GUI 選択のテスト。"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest

from log_visualizer.analyzer.analyzer_factory import CachedSourceAnalysis
from log_visualizer.analyzer.raw_model import (
    RawCall,
    RawFileAnalysis,
    RawFunction,
    merge_analyses,
)
from log_visualizer.cli import main
from log_visualizer.config.settings import Settings
from log_visualizer.gui import select_inputs
from log_visualizer.parser.source_tree import DirectorySourceReader, decode_source


def write(root: Path, rel: str, text: str, encoding: str = "utf-8") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    return path


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    write(root, "src/app/main.c", "int main(void)\n{\n    helper();\n    return 0;\n}\n")
    write(root, "src/app/helper.c", "/* 日本語コメント */\nvoid helper(void)\n{\n}\n", "cp932")
    write(root, "src/app/test/test_main.c", "void test(void) {}\n")
    write(root, "src/app/README.md", "not a source\n")
    write(root, ".git/objects/x.c", "void git_object(void) {}\n")
    write(root, "build/CMakeFiles/id.c", "int x;\n")
    return root


def test_decode_source() -> None:
    assert decode_source("日本語".encode("utf-8")) == "日本語"
    assert decode_source("日本語".encode("cp932")) == "日本語"
    assert decode_source(b"\xef\xbb\xbfint x;") == "int x;"


def test_recursive_walk_with_exclusions(settings: Settings, tree: Path) -> None:
    reader = DirectorySourceReader(tree, settings.source)
    paths = [f.path for f in reader.iter_files()]
    assert paths == ["src/app/helper.c", "src/app/main.c"]
    helper = next(f for f in reader.iter_files() if f.path.endswith("helper.c"))
    assert "日本語コメント" in helper.content  # CP932 を自動判定
    # include 解決用の全ファイル（除外パターン無視。.git は常に除外）
    all_paths = {f.path for f in reader.iter_all()}
    assert "src/app/test/test_main.c" in all_paths
    assert not any(p.startswith(".git/") for p in all_paths)
    assert set(reader.signatures()) == {"src/app/helper.c", "src/app/main.c"}
    assert reader.local_root() == tree.resolve()


def test_incremental_cache(tmp_settings: Settings, tree: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    reader = DirectorySourceReader(tree, tmp_settings.source)
    cache = CachedSourceAnalysis(tmp_settings, reader)

    _, first = cache.load("regex")
    assert {f.name for r in first for f in r.functions} == {"main", "helper"}

    caplog.clear()
    _, second = cache.load("regex")
    assert "解析が必要 0" in caplog.text
    assert len(second) == len(first)

    # 1 ファイルだけ変更 → そのファイルだけ再解析
    changed = write(tree, "src/app/helper.c", "void helper(void)\n{\n}\nvoid added(void)\n{\n}\n")
    os.utime(changed, ns=(time.time_ns(), time.time_ns() + 10_000_000))
    caplog.clear()
    _, third = cache.load("regex")
    assert "解析が必要 1" in caplog.text
    assert {f.name for r in third for f in r.functions} == {"main", "helper", "added"}

    # 削除されたファイルは結果から消える
    (tree / "src/app/main.c").unlink()
    _, fourth = cache.load("regex")
    assert {f.name for r in fourth for f in r.functions} == {"helper", "added"}


def test_merge_analyses_dedupes_header_functions() -> None:
    def unit() -> RawFileAnalysis:
        return RawFileAnalysis(
            path="inc/util.h",
            functions=[RawFunction("inline_fn", 3, 5, True)],
            calls=[RawCall(0, "other", 4)],
        )

    merged = merge_analyses([unit(), unit(), RawFileAnalysis(path="a.c", functions=[RawFunction("f", 1, 2, False)])])
    by_path = {m.path: m for m in merged}
    assert len(by_path["inc/util.h"].functions) == 1
    assert len(by_path["inc/util.h"].calls) == 1
    assert len(by_path["a.c"].functions) == 1


def test_cli_with_source_folder(sample_log: Path, sample_src: Path, tmp_path: Path) -> None:
    """ダンプではなく実ソースフォルダを指定して実行できる。"""
    from .test_renderer import read_payload

    out = tmp_path / "out"
    code = main([
        "--log", str(sample_log), "--source", str(sample_src), "--out", str(out),
        "--backend", "regex", "--cache-dir", str(tmp_path / "cache"),
    ])
    assert code == 0
    payload = read_payload(out / "sequence.html")
    assert "src/ingress/ingress_task1.c" in payload["sources"]
    assert any(e[11] > 0 for e in payload["events"])  # 矢印あり


class FakeDialogs:
    def __init__(self, files: list[str], dirs: list[str], yes: bool = False) -> None:
        self.files = files
        self.dirs = dirs
        self.yes = yes
        self.titles: list[str] = []

    def open_file(self, title: str, initial_dir: str, filetypes: list[tuple[str, str]]) -> str:
        self.titles.append(title)
        return self.files.pop(0) if self.files else ""

    def directory(self, title: str, initial_dir: str) -> str:
        self.titles.append(title)
        return self.dirs.pop(0) if self.dirs else ""

    def yes_no(self, title: str, message: str) -> bool:
        return self.yes

    def info(self, title: str, message: str) -> None:
        pass

    def error(self, title: str, message: str) -> None:
        pass


def test_gui_selection(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    log = tmp_path / "logs" / "log.txt"
    dialogs = FakeDialogs(files=[str(log)], dirs=[str(tmp_path / "src"), ""])
    sel = select_inputs(dialogs, state_path=state)
    assert sel is not None
    assert sel.log == log and sel.source == tmp_path / "src"
    assert sel.out == log.parent / "lv_output"  # 出力先キャンセル → 既定
    assert state.exists()

    # ログを選ばなければ中止
    assert select_inputs(FakeDialogs(files=[], dirs=[]), state_path=state) is None

    # ソースフォルダをキャンセルし「いいえ」→ ログのみ
    sel = select_inputs(FakeDialogs(files=[str(log)], dirs=["", str(tmp_path)]), state_path=state)
    assert sel is not None and sel.source is None and sel.out == tmp_path

    # 指定済みの項目はダイアログを出さない
    dialogs = FakeDialogs(files=[], dirs=[])
    sel = select_inputs(dialogs, log=log, source=tmp_path, out=tmp_path, state_path=state)
    assert sel is not None and dialogs.titles == []
