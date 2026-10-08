/**
 * @file task_manager.c
 * @brief JobManager: タスク生成と実行（サンプル）
 */
#include "os_api.h"
#include "utility_log.h"
#include "task_manager.h"

static const TaskCallbacks *s_callbacks;
static Task s_tasks[8];
static int s_next_generation = 1;

static void handler_print(Task *task);
static void handler_maintenance(Task *task);

static TaskHandler lookup_handler(int type)
{
    return type == 1 ? handler_print : handler_maintenance;
}

static void handler_print(Task *task)
{
    UT_LOG_INFO("JobManager", "print handler task=%d", task->id);
}

static void handler_maintenance(Task *task)
{
    UT_LOG_INFO("JobManager", "maintenance handler task=%d", task->id);
}

static void process_job(Task *task)
{
    TaskHandler handler = lookup_handler(task->type);

    UT_LOG_INFO("JobManager", "process job task=%d type=%d", task->id, task->type);
    (*handler)(task);
    if (s_callbacks != 0)
    {
        s_callbacks->on_done(task->id);
    }
}

static void *task_worker(void *arg)
{
    Task *task = (Task *)arg;
    UT_LOG_INFO("JobManager", "worker started task=%d", task->id);
    process_job(task);
    return 0;
}

static void execute_task(Task *task)
{
    pthread_t thread;
    UT_LOG_INFO("JobManager", "execute task=%d", task->id);
    if (pthread_create(&thread, 0, task_worker, task) != 0)
    {
        UT_LOG_ERROR("JobManager", "pthread_create failed task=%d", task->id);
    }
}

int create_task(int request_id, int type)
{
    Task *task = &s_tasks[s_next_generation % 8];
    task->id = s_next_generation++;
    task->type = type;
    UT_LOG_INFO("JobManager", "create task=%d request=%d", task->id, request_id);
    execute_task(task);
    return task->id;
}

void task_manager_register(const TaskCallbacks *callbacks)
{
    s_callbacks = callbacks;
}

void task_manager_init(void)
{
    UT_LOG_INFO("JobManager", "task manager initialized");
}
