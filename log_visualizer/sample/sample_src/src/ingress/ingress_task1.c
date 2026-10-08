/**
 * @file ingress_task1.c
 * @brief 要求受付タスク（サンプル）
 */
#include "os_api.h"
#include "utility_log.h"
#include "ingress_internal.h"
#include "task_manager.h"
#include "eif_notify.h"

static void on_task_done(int task_id);
static void on_task_error(int task_id, int code);

/* コールバック登録（指示付き初期化子） */
static const TaskCallbacks k_callbacks = {
    .on_done = on_task_done,
    .on_error = on_task_error,
};

static void handle_submit(const Request *req)
{
    int generation_id;

    UT_LOG_INFO("PCL:handle_submit()", "task1=submit begin product_request_id=%d", req->id);
    generation_id = create_task(req->id, req->type);
    if (generation_id < 0)
    {
        UT_LOG_ERROR("PCL:handle_submit()", "task1=submit create_task failed product_request_id=%d", req->id);
        return;
    }
    UT_LOG_INFO("PCL:handle_submit()", "task1=submit task2 queued generation_id=%d", generation_id);
}

static void ingress_task1_main(void *arg)
{
    Request req;
    (void)arg;
    for (;;)
    {
        if (OsMsgQReceive(s_ingress.requestQueue, &req, sizeof(req), OS_WAIT_FOREVER) != 0)
        {
            continue;
        }
        UT_LOG_INFO("PCL:EVENT-TASK1", "Dispatching event of type %d", req.type);
        handle_submit(&req);
    }
}

static void on_task_done(int task_id)
{
    UT_LOG_INFO("PCL:completion", "completion generation_id=%d execution_result=0", task_id);
    eif_notify_completion(task_id);
}

static void on_task_error(int task_id, int code)
{
    UT_LOG_WARN("PCL:completion", "completion failed generation_id=%d code=%d", task_id, code);
}

void ingress_init(void)
{
    OsTaskAttr attr = { 10, 4096 };
    s_ingress.requestQueue = OsMsgQCreate(16, sizeof(Request), 0);
    task_manager_register(&k_callbacks);
    (void)OsTaskCreate(&s_ingress.task1, ingress_task1_main, &attr);
    UT_LOG_INFO("PCL:init", "ingress initialized");
}
