#ifndef VOS3_NATIVE_ISOLATION_TEST_H
#define VOS3_NATIVE_ISOLATION_TEST_H
/* Only linked into explicitly requested diagnostic images. No user ABI. */
#ifdef NATIVE_ISOLATION_TEST
#include "task.h"
const char* vos3_native_isolation_target(void);
void vos3_native_isolation_mapping(vos3_task_t* task, uintptr_t address, uintptr_t phys);
void vos3_native_isolation_fault(vos3_task_t* task, uintptr_t address);
#endif
#endif
