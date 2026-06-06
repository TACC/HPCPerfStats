#ifndef _INTEL_PROCESSOR_H_
#define _INTEL_PROCESSOR_H_

#include "cpuid.h"

int intel_processor_is_intel(processor_t p);
int intel_processor_is_skx_server(processor_t p);
int intel_processor_is_icx(processor_t p);
int intel_processor_is_spr(processor_t p);

#endif
