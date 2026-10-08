/**
 * @file eif_notify.c
 * @brief 外部 I/F（EIF）への完了通知（サンプル）
 */
#include "os_api.h"
#include "utility_log.h"
#include "eif_notify.h"

#define EIF_EV_DONE 0x0001u

static os_task_t s_eif_task;
static volatile int s_last_task_id;

static void eif_handle_event(uint32_t events)
{
    UT_LOG_INFO("EIF", "event handled events=0x%x task=%d -> websocket", events, s_last_task_id);
}

static void eif_task_main(void *arg)
{
    (void)arg;
    for (;;)
    {
        uint32_t events = OsEventReceive(EIF_EV_DONE, OS_WAIT_FOREVER);
        eif_handle_event(events);
    }
}

void eif_notify_completion(int task_id)
{
    s_last_task_id = task_id;
    UT_LOG_INFO("EIF", "notify completion task=%d", task_id);
    (void)OsEventSend(s_eif_task, EIF_EV_DONE);
}

void eif_init(void)
{
    OsTaskAttr attr = { 20, 4096 };
    (void)OsTaskCreate(&s_eif_task, eif_task_main, &attr);
}
