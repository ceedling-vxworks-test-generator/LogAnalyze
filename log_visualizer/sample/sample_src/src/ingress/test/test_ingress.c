/* テストコード（既定設定では解析対象から除外される） */
#include "ingress_internal.h"

void test_submit(void)
{
    (void)ExternalRequestIngressSubmit(99);
}
