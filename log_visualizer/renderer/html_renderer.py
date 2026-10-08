"""単一 HTML の生成。

template.html に CSS / JS / viz.js をインライン化し、payload（gzip）を base64 で埋め込む。
payload はチャンク単位で書き出すため、巨大でも Python 側で全体を保持しない。
"""

from __future__ import annotations

import base64
import html
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from ..config.settings import RenderSettings
from ..domain.models import CallGraph, SequenceEvent, SourceFile
from ..domain.ports import SourceRepository
from .payload_writer import PayloadWriter

LOG = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
PAYLOAD_MARKER = "__LV_PAYLOAD__"
CHUNK = 3 * 1024 * 256  # base64 の境界を揃えるため 3 の倍数


def _inline_script(text: str) -> str:
    return text.replace("</script", "<\\/script")


class HtmlRenderer:
    def __init__(self, settings: RenderSettings, ui_dir: Path = UI_DIR) -> None:
        self._settings = settings
        self._ui_dir = ui_dir

    def render(
        self,
        events: Iterable[SequenceEvent],
        graph: CallGraph,
        sources: SourceRepository,
        out_path: Path,
        title: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        writer = PayloadWriter(graph, out_path.parent, self._settings.max_message_length)
        try:
            for event in events:
                writer.add(event)
                if writer.count % 100000 == 0:
                    LOG.info("  %d イベント処理済み", writer.count)
            embedded = self._select_sources(graph, writer.referenced_fids, writer.referenced_files)
            LOG.info("ソースビューア用に %d ファイルを埋め込み", len(embedded))
            full_meta: dict[str, Any] = {
                "title": title or "Log Sequence Viewer",
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            }
            full_meta.update(meta or {})
            payload = writer.finish(full_meta, self._iter_sources(sources, embedded))
            self._write_html(out_path, payload, full_meta["title"])
        finally:
            writer.discard()
        return out_path

    def _select_sources(
        self, graph: CallGraph, fids: set[str], file_names: set[str]
    ) -> set[str]:
        mode = self._settings.embed_sources
        if mode == "none":
            return set()
        if mode == "all":
            return {f.file_path for f in graph.functions.values()}
        paths = {graph.functions[fid].file_path for fid in fids if fid in graph.functions}
        wanted = {name.lower() for name in file_names}
        if wanted:
            for info in graph.functions.values():
                if info.basename.lower() in wanted:
                    paths.add(info.file_path)
        return paths

    def _iter_sources(
        self, sources: SourceRepository, paths: set[str]
    ) -> Iterable[SourceFile]:
        if not paths:
            return []
        if self._settings.embed_sources == "all":
            return sources.iter_files()
        return sources.iter_selected(paths)

    def _write_html(self, out_path: Path, payload: Path, title: str) -> None:
        template = (self._ui_dir / "template.html").read_text(encoding="utf-8")
        css = (self._ui_dir / "app.css").read_text(encoding="utf-8")
        app_js = (self._ui_dir / "app.js").read_text(encoding="utf-8")
        viz_path = self._ui_dir / "vendor" / "viz-standalone.js"
        viz_js = viz_path.read_text(encoding="utf-8") if viz_path.exists() else ""

        page = (
            template.replace("__LV_TITLE__", html.escape(title))
            .replace("/*__LV_CSS__*/", css)
            .replace("/*__LV_VIZ__*/", _inline_script(viz_js))
            .replace("/*__LV_APP__*/", _inline_script(app_js))
        )
        head, sep, tail = page.partition(PAYLOAD_MARKER)
        if not sep:
            raise ValueError("template.html に payload マーカーがありません")
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as out, payload.open("rb") as src:
            out.write(head)
            while True:
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                out.write(base64.b64encode(chunk).decode("ascii"))
            out.write(tail)
        tmp.replace(out_path)
