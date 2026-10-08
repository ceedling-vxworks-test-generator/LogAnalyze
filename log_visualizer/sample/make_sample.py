"""サンプルのソースダンプ（sample_source_dump.txt）とログ（sample.log）を生成する。

    python make_sample.py            # sample_src/ から同じディレクトリに生成
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
src_root = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "sample_src"
out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE
BS = chr(92)

files = sorted(p for p in src_root.rglob("*") if p.is_file())
texts = {p.relative_to(src_root).as_posix(): p.read_text(encoding="utf-8") for p in files}

with (out_dir / "sample_source_dump.txt").open("w", encoding="utf-8", newline="\n") as fp:
    for rel, text in texts.items():
        fp.write("===== FILE BEGIN =====\n")
        fp.write("Path: Input" + BS + rel.replace("/", BS) + "\n")
        fp.write("===== CONTENT BEGIN =====\n")
        fp.write(text if text.endswith("\n") else text + "\n")
        fp.write("===== CONTENT END =====\n")
        fp.write("===== FILE END =====\n\n")

def where(file, marker):
    rel = next(k for k in texts if k.endswith("/" + file))
    for no, line in enumerate(texts[rel].splitlines(), 1):
        if marker in line:
            return no
    raise SystemExit(f"marker not found: {file}: {marker}")

def log(ts, level, module, msg, file, func, marker):
    return f"[{ts}] [0] {level:<5} {module:<23} {msg} ({file}:{where(file, marker)} {func})"

def raw(ts, text):
    return f"[{ts}] {text}"

D = "2026-10-07 "
L = []
L += [raw(D + "14:37:06.100", "================================="),
      raw(D + "14:37:06.100", " Application Starting (sample)"),
      raw(D + "14:37:06.100", "=================================")]
L.append(log(D + "14:37:06.894", "INFO", "JobManager", "task manager initialized", "task_manager.c", "task_manager_init", '"task manager initialized"'))
L.append(log(D + "14:37:06.894", "INFO", "PCL:init", "ingress initialized", "ingress_task1.c", "ingress_init", '"ingress initialized"'))

def request(t, rid, gen, handler="print", fail=False, interleave=False):
    out = []
    out.append(log(D + t[0], "INFO", "PCL:API request Start", f"api=submit begin product_request_id={rid}", "ingress_api.c", "ExternalRequestIngressSubmit", "api=submit begin"))
    end = log(D + t[0], "INFO", "PCL:API request End", f"api=submit end api_result=0 product_request_id={rid}", "ingress_api.c", "ExternalRequestIngressSubmit", "api=submit end api_result")
    if interleave:
        # 別出力が行の途中に割り込み、末尾 ")" が次の行に回るケース
        head = end[:-len(" ExternalRequestIngressSubmit)")]
        out.append(head + " ExternalRequestIngressSu*****************Adapter to store DataID=7[Dispatch(" + str(where("rim_adapter.c", "RIM_TRACE(\"Adapter")) + ")]")
        out.append(raw(D + t[1], "bmit)"))
    else:
        out.append(end)
    out.append(log(D + t[1], "INFO", "PCL:EVENT-TASK1", "Dispatching event of type 1", "ingress_task1.c", "ingress_task1_main", "Dispatching event"))
    out.append(log(D + t[1], "INFO", "PCL:handle_submit()", f"task1=submit begin product_request_id={rid}", "ingress_task1.c", "handle_submit", "task1=submit begin"))
    out.append(log(D + t[1], "INFO", "JobManager", f"create task={gen} request={rid}", "task_manager.c", "create_task", '"create task='))
    out.append(log(D + t[2], "INFO", "JobManager", f"execute task={gen}", "task_manager.c", "execute_task", '"execute task='))
    if fail:
        out.append(log(D + t[2], "ERROR", "JobManager", f"pthread_create failed task={gen}", "task_manager.c", "execute_task", "pthread_create failed"))
        out.append(log(D + t[2], "INFO", "PCL:handle_submit()", f"task1=submit task2 queued generation_id={gen}", "ingress_task1.c", "handle_submit", "task2 queued"))
        out.append(log(D + t[3], "WARN", "PCL:completion", f"completion failed generation_id={gen} code=5", "ingress_task1.c", "on_task_error", "completion failed"))
        return out
    out.append(log(D + t[2], "INFO", "PCL:handle_submit()", f"task1=submit task2 queued generation_id={gen}", "ingress_task1.c", "handle_submit", "task2 queued"))
    out.append(log(D + t[3], "INFO", "JobManager", f"worker started task={gen}", "task_manager.c", "task_worker", "worker started"))
    out.append(log(D + t[3], "INFO", "JobManager", f"process job task={gen} type=1", "task_manager.c", "process_job", "process job"))
    hfunc = "handler_print" if handler == "print" else "handler_maintenance"
    out.append(log(D + t[3], "INFO", "JobManager", f"{handler} handler task={gen}", "task_manager.c", hfunc, f'"{handler} handler'))
    out.append(log(D + t[4], "INFO", "PCL:completion", f"completion generation_id={gen} execution_result=0", "ingress_task1.c", "on_task_done", "completion generation_id"))
    out.append(log(D + t[4], "INFO", "EIF", f"notify completion task={gen}", "eif_notify.c", "eif_notify_completion", "notify completion"))
    # RIM: 改行入りメッセージ（末尾情報が次の行に回る）
    rim_line = where("rim_adapter.c", "pipeline status updated")
    out.append(f"[{D}{t[4]}] [0] INFO  {'RIM':<23} *****************pipeline status updated pipeline=0 busy=0 [RimUpdateStatus({rim_line})]")
    out.append(f"[{D}{t[4]}]  (rim_adapter.c:{rim_line} RimUpdateStatus)")
    out.append(raw(D + t[4], "*****************Adapter to store DataID=4[Dispatch(" + str(where("rim_adapter.c", "RIM_TRACE(\"Adapter")) + ")]"))
    out.append(log(D + t[5], "INFO", "EIF", f"event handled events=0x1 task={gen} -> websocket", "eif_notify.c", "eif_handle_event", "event handled"))
    return out

L += request(["14:37:14.245", "14:37:14.260", "14:37:14.276", "14:37:14.291", "14:37:14.307", "14:37:14.322"], 1, 1)
L += request(["14:37:15.281", "14:37:15.296", "14:37:15.312", "14:37:15.328", "14:37:15.343", "14:37:15.359"], 5, 2, handler="maintenance", interleave=True)
L += request(["14:37:16.010", "14:37:16.025", "14:37:16.041", "14:37:16.057"], 6, 3, fail=True)
L.append(raw(D + "14:37:17.000", "thread '<unnamed>' (538) panicked at src/server.rs:164:29:"))
L.append(raw(D + "14:37:17.000", "failed to bind 0.0.0.0:8000"))

(out_dir / "sample.log").write_text("\r\n".join(L) + "\r\n", encoding="utf-8", newline="")
print(len(L), "lines")
