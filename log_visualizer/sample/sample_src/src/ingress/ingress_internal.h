/**
 * @file ingress_internal.h
 */
#ifndef INGRESS_INTERNAL_H
#define INGRESS_INTERNAL_H

#include "os_api.h"

typedef struct Request {
    int id;
    int type;
} Request;

typedef struct IngressContext {
    MSG_Q_ID requestQueue;
    os_task_t task1;
} IngressContext;

extern IngressContext s_ingress;

int ExternalRequestIngressSubmit(int request_id);
void ingress_init(void);

#endif /* INGRESS_INTERNAL_H */
