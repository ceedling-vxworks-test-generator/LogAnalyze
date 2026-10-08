/**
 * @file task_manager.h
 * @brief JobManager の公開 API（サンプル）
 */
#ifndef TASK_MANAGER_H
#define TASK_MANAGER_H

typedef struct Task {
    int id;
    int type;
} Task;

/** 完了通知コールバック群 */
typedef struct TaskCallbacks {
    void (*on_done)(int task_id);
    void (*on_error)(int task_id, int code);
} TaskCallbacks;

typedef void (*TaskHandler)(Task *task);

void task_manager_init(void);
void task_manager_register(const TaskCallbacks *callbacks);
int create_task(int request_id, int type);

#endif /* TASK_MANAGER_H */
