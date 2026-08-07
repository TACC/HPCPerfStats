#include "intel_processor.h"

int intel_processor_is_intel(processor_t p)
{
  return p >= NEHALEM && p <= SIERRA_FOREST;
}

int intel_processor_is_skx_server(processor_t p)
{
  return p == SKYLAKE_X || p == CASCADE_LAKE;
}

int intel_processor_is_icx(processor_t p)
{
  return p == ICELAKE_SERVER;
}

int intel_processor_is_spr(processor_t p)
{
  return p == SAPPHIRE_RAPIDS;
}

int intel_processor_is_emr(processor_t p)
{
  return p == EMERALD_RAPIDS;
}

int intel_processor_is_gnr(processor_t p)
{
  return p == GRANITE_RAPIDS;
}

int intel_processor_is_srf(processor_t p)
{
  return p == SIERRA_FOREST;
}
