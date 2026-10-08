/**
 * @file utility_log.h
 * @brief ログ出力マクロ（サンプル）
 *
 * 出力書式: [%llu] %-5s %-23s %s (%s:%u %s)
 */
#ifndef UTILITY_LOG_H
#define UTILITY_LOG_H

#include <stdint.h>

typedef enum { UT_LOG_LEVEL_TRACE, UT_LOG_LEVEL_DEBUG, UT_LOG_LEVEL_INFO, UT_LOG_LEVEL_WARN, UT_LOG_LEVEL_ERROR } ut_log_level_t;

int ut_log_write_default(ut_log_level_t level, const char *module, const char *file,
                         uint32_t line, const char *func, const char *fmt, ...);

#define UT_LOG_INFO(module, ...) \
    (void)ut_log_write_default(UT_LOG_LEVEL_INFO, (module), __FILE__, (uint32_t)__LINE__, __func__, __VA_ARGS__)
#define UT_LOG_WARN(module, ...) \
    (void)ut_log_write_default(UT_LOG_LEVEL_WARN, (module), __FILE__, (uint32_t)__LINE__, __func__, __VA_ARGS__)
#define UT_LOG_ERROR(module, ...) \
    (void)ut_log_write_default(UT_LOG_LEVEL_ERROR, (module), __FILE__, (uint32_t)__LINE__, __func__, __VA_ARGS__)

#endif /* UTILITY_LOG_H */
