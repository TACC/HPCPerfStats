#include "amd_processor.h"

int amd_processor_is_epyc(processor_t p)
{
  return p == AMD_ROME || p == AMD_MILAN || p == AMD_GENOA || p == AMD_TURIN;
}

int amd_processor_is_rome(processor_t p)
{
  return p == AMD_ROME;
}

int amd_processor_is_milan(processor_t p)
{
  return p == AMD_MILAN;
}

int amd_processor_is_genoa(processor_t p)
{
  return p == AMD_GENOA;
}

int amd_processor_is_turin(processor_t p)
{
  return p == AMD_TURIN;
}
