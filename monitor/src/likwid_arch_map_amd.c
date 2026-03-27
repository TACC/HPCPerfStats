#include "likwid_arch_map.h"

const char *likwid_arch_eventset(void)
{
  return "RETIRED_INSTRUCTIONS:PMC0,RETIRED_BRANCH_INSTR:PMC1,RETIRED_MISP_BRANCH_INSTR:PMC2,"
         "LS_DISPATCH:PMC3";
}
