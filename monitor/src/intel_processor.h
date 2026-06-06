#ifndef _INTEL_PROCESSOR_H_
#define _INTEL_PROCESSOR_H_

#include "cpuid.h"

int intel_processor_is_intel(processor_t p);
int intel_processor_is_skx_server(processor_t p);
int intel_processor_is_icx(processor_t p);
int intel_processor_is_spr(processor_t p);
int intel_processor_is_snb_ep(processor_t p);
int intel_processor_is_ivb_ep(processor_t p);
int intel_processor_is_hsw_ep(processor_t p);
int intel_processor_is_bdw_ep(processor_t p);

#endif
