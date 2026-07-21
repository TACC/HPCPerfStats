#ifndef _AMD_PROCESSOR_H_
#define _AMD_PROCESSOR_H_

#include "cpuid.h"

int amd_processor_is_epyc(processor_t p);
int amd_processor_is_rome(processor_t p);
int amd_processor_is_milan(processor_t p);
int amd_processor_is_genoa(processor_t p);
int amd_processor_is_turin(processor_t p);

#endif
