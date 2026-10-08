"""HTML 出力・CLI のテスト。"""

from __future__ import annotations

import base64
import gzip
import json
import re
from pathlib import Path
from typing import Any

from log_visualizer.cli import main
from log_visualizer.renderer.payload_writer import EVENT_COLUMNS


def read_payload(html_path: Path) -> dict[str, Any]:
    html = html_path.read_text(encoding="utf-8")
    m = re.search(r'<script id="lv-payload" type="application/octet-stream">([^<]+)</script>', html)
    assert m is not None
    data: dict[str, Any] = json.loads(gzip.decompress(base64.b64decode(m.group(1))))
    return data


def test_cli_end_to_end(sample_log: Path, sample_dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    code = main(
        [
            "--log", str(sample_log),
            "--source", str(sample_dump),
            "--out", str(out),
            "--backend", "regex",
            "--cache-dir", str(tmp_path / "cache"),
        ]
    )
    assert code == 0
    for name in ("sequence.html", "callgraph.json", "callgraph_detail.json", "callgraph.dot"):
        assert (out / name).exists()

    html = (out / "sequence.html").read_text(encoding="utf-8")
    assert "<script src=" not in html  # 外部リソースなし（オフライン）
    assert "/*__LV_APP__*/" not in html and "__LV_PAYLOAD__" not in html

    payload = read_payload(out / "sequence.html")
    assert payload["columns"] == EVENT_COLUMNS
    assert payload["meta"]["backend"] == "regex"
    events = payload["events"]
    assert len(events) == payload["meta"]["event_count"] > 40
    funcs = payload["strings"]["func"]
    names = [funcs[e[6]] if e[6] >= 0 else None for e in events]
    assert "handle_submit" in names
    # 矢印（親）が設定されているイベントがある
    assert any(e[10] >= 0 and e[11] > 0 for e in events)
    # ログに出現したファイルのソースが埋め込まれている
    assert "src/ingress/ingress_task1.c" in payload["sources"]
    assert "ingress_task1.c::handle_submit" in payload["graph"]["nodes"]


def test_cli_log_only_with_filters(sample_log: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    code = main(
        [
            "--log", str(sample_log),
            "--out", str(out),
            "--from", "2026-10-07 14:37:14.000",
            "--to", "2026-10-07 14:37:15.000",
            "--module", "JobManager",
        ]
    )
    assert code == 0
    payload = read_payload(out / "sequence.html")
    lanes = payload["lanes"]
    assert lanes == ["JobManager"]
    assert payload["meta"]["backend"] == "none"
    assert all(e[9] == -1 for e in payload["events"])  # ソースなしなので関数 ID 未解決


def test_cli_missing_log(tmp_path: Path) -> None:
    assert main(["--log", str(tmp_path / "none.log")]) == 2
