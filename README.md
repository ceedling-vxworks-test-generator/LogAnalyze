# LogAnalyze

大規模 C/C++ プロジェクトのログとソースコードを解析し、実行フローをシーケンス図（単一 HTML）で可視化するツール `log_visualizer` です。

- 使い方: [log_visualizer/README.md](log_visualizer/README.md)
- 設計書: [log_visualizer/docs/architecture.md](log_visualizer/docs/architecture.md)

```bash
pip install -r log_visualizer/requirements.txt
python -m log_visualizer --log log_visualizer/sample/sample.log --source log_visualizer/sample/sample_source_dump.txt --out lv_output
```
