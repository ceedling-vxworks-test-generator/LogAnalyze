"""ソースダンプ読込みテスト。"""

from __future__ import annotations

from pathlib import Path

from log_visualizer.config.settings import Settings
from log_visualizer.parser.source_dump import PathFilter, SourceDumpReader, parse_dump_lines

DUMP = """===== FILE BEGIN =====
Path: Input\\src\\app\\main.c
===== CONTENT BEGIN =====
int main(void)
{
    /* 本文中のマーカー行は終端とみなさない */
===== CONTENT END =====
    return 0;
}
===== CONTENT END =====
===== FILE END =====

===== FILE BEGIN =====
Path: Input\\build\\x64\\CMakeFiles\\CompilerIdC\\CMakeCCompilerId.c
===== CONTENT BEGIN =====
int x;
===== CONTENT END =====
===== FILE END =====

===== FILE BEGIN =====
Path: Input\\src\\app\\test\\test_main.c
===== CONTENT BEGIN =====
void test(void) {}
===== CONTENT END =====
===== FILE END =====
"""


def test_parse_and_normalize(settings: Settings) -> None:
    files = list(parse_dump_lines(DUMP.splitlines(keepends=True), PathFilter(settings.source)))
    assert [f.path for f in files] == [
        "src/app/main.c",
        "build/x64/CMakeFiles/CompilerIdC/CMakeCCompilerId.c",
        "src/app/test/test_main.c",
    ]
    assert "===== CONTENT END =====\n    return 0;" in files[0].content
    assert files[0].basename == "main.c"


def test_exclusion(settings: Settings, tmp_path: Path) -> None:
    path = tmp_path / "dump.txt"
    path.write_text(DUMP, encoding="utf-8")
    reader = SourceDumpReader(path, settings.source)
    assert [f.path for f in reader.iter_files()] == ["src/app/main.c"]
    assert len(list(reader.iter_all())) == 3
    selected = list(reader.iter_selected(["src/app/test/test_main.c"]))
    assert [f.path for f in selected] == ["src/app/test/test_main.c"]
    assert len(reader.fingerprint()) == 24


def test_sample_dump(settings: Settings, sample_dump: Path) -> None:
    reader = SourceDumpReader(sample_dump, settings.source)
    paths = [f.path for f in reader.iter_files()]
    assert "src/ingress/ingress_task1.c" in paths
    assert not any("/test/" in p for p in paths)
