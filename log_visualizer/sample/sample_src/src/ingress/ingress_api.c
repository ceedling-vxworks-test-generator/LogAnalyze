/**
 * @file ingress_api.c
 * @brief 外部要求の受付 API（サンプル）
 */
#include "os_api.h"
#include "utility_log.h"
#include "ingress_internal.h"

IngressContext s_ingress;

int ExternalRequestIngressSubmit(int request_id)
{
    Request req;
    req.id = request_id;
    req.type = 1;
    UT_LOG_INFO("PCL:API request Start", "api=submit begin product_request_id=%d", request_id);

    if (OsMsgQSend(s_ingress.requestQueue, &req, sizeof(req), OS_NO_WAIT, 0) != 0)
    {
        UT_LOG_ERROR("PCL:API request End", "api=submit queue full product_request_id=%d", request_id);
        return -1;
    }
    UT_LOG_INFO("PCL:API request End", "api=submit end api_result=0 product_request_id=%d", request_id);
    return 0;
}
