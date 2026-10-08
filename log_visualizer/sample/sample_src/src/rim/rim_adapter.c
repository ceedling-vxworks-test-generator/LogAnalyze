/**
 * @file rim_adapter.c
 * @brief RIM アダプタ（printf 形式のトレースを出す例）
 */
#include <stdio.h>
#include "utility_log.h"

#define RIM_TRACE(fmt, ...) printf("*****************" fmt "[%s(%d)]\n", ##__VA_ARGS__, __func__, __LINE__)

void Dispatch(int data_id)
{
    RIM_TRACE("Adapter to store DataID=%d", data_id);
}

void RimUpdateStatus(int pipeline, int busy)
{
    UT_LOG_INFO("RIM", "pipeline status updated pipeline=%d busy=%d", pipeline, busy);
    Dispatch(4);
}
