#include "intel_processor.h"

int intel_processor_is_intel(processor_t p)
{
  return p >= NEHALEM && p <= SAPPHIRE_RAPIDS;
}

int intel_processor_is_skx_server(processor_t p)
{
  return p == CASCADE_LAKE;
}

int intel_processor_is_icx(processor_t p)
{
  return p == ICELAKE_SERVER;
}

int intel_processor_is_spr(processor_t p)
{
  return p == SAPPHIRE_RAPIDS;
}

int intel_processor_is_snb_ep(processor_t p)
{
  return p == SANDYBRIDGE;
}

int intel_processor_is_ivb_ep(processor_t p)
{
  return p == IVYBRIDGE;
}

int intel_processor_is_hsw_ep(processor_t p)
{
  return p == HASWELL;
}

int intel_processor_is_bdw_ep(processor_t p)
{
  return p == BROADWELL;
}
