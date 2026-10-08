/**
 * @file os_api.h
 * @brief OS 抽象化レイヤ（サンプル）
 */
#ifndef OS_API_H
#define OS_API_H

#include <stdint.h>

typedef void *MSG_Q_ID;
typedef uint32_t os_task_t;
typedef void (*OS_TASKFUNC)(void *arg);
typedef struct { int priority; int stack_size; } OsTaskAttr;
typedef unsigned long pthread_t;

#define OS_WAIT_FOREVER 0xFFFFFFFFu
#define OS_NO_WAIT 0u

extern MSG_Q_ID OsMsgQCreate(int maxMsgs, int maxMsgLength, int options);
extern int OsMsgQSend(MSG_Q_ID queueId, void *buffer, unsigned int nBytes, unsigned int timeout, int priority);
extern int OsMsgQReceive(MSG_Q_ID queueId, void *buffer, unsigned int maxNBytes, unsigned int timeout);
extern int OsTaskCreate(os_task_t *taskid, OS_TASKFUNC func, OsTaskAttr *attr);
extern uint32_t OsEventSend(os_task_t task, uint32_t event);
extern uint32_t OsEventReceive(uint32_t events, uint32_t timeout);
extern int pthread_create(pthread_t *thread, const void *attr, void *(*start)(void *), void *arg);

#endif /* OS_API_H */
