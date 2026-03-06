/* Currently only used for intel chips after Nehalem - x2APIC chips */
/* nhm, wtm, snb, ivb, hsw, bdw, knl, skx are classified correctly */
#ifndef _CPUID_H_
#define _CPUID_H_

typedef enum { 
  AMD_10H, AMD_17H, AMD_19H,
  NEHALEM, WESTMERE, 
  SANDYBRIDGE, IVYBRIDGE, 
  HASWELL, BROADWELL, KNL,
  SKYLAKE, ARM9
} processor_t;
  
// Return 1 for true and 0 for false
int percore_signature(processor_t p, char *cpu, int *nr_events);
processor_t signature(int *n_pmcs);
int topology(char *cpu, int *pkg, int *core, int *smt, int *nr_core);

#endif
