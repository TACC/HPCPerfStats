/* Currently only used for intel chips after Nehalem - x2APIC chips */
/* nhm, wtm, snb, ivb, hsw, bdw, skx are classified correctly */
#ifndef _CPUID_H_
#define _CPUID_H_

typedef enum {
  AMD_10H,   /* legacy enum; signature() never returns it */
  AMD_ROME,  /* EPYC Zen2 Fam17h Models 30h-3Fh */
  AMD_MILAN, /* EPYC Zen3 Fam19h Models 00h-0Fh */
  AMD_GENOA, /* EPYC Zen4 Genoa/Bergamo/Siena */
  AMD_TURIN, /* EPYC Zen5 Fam1Ah Models 00h-1Fh */
  NEHALEM,
  WESTMERE,
  SANDYBRIDGE,
  IVYBRIDGE,
  HASWELL,
  BROADWELL,
  SKYLAKE,      /* client 06_4e / 06_5e */
  SKYLAKE_X,    /* server 06_55 stepping < 5 (LIKWID skylakeX) */
  CASCADE_LAKE, /* server 06_55 stepping >= 5 (LIKWID CLX; Cooper Lake) */
  ICELAKE_SERVER,
  SAPPHIRE_RAPIDS,
  EMERALD_RAPIDS,
  GRANITE_RAPIDS,
  SIERRA_FOREST
} processor_t;

/* Return 1 for true and 0 for false */
int percore_signature(processor_t p, char *cpu, int *nr_events);
processor_t signature(int *n_pmcs);
int cpuid_read_cpu_topology(char *cpu, int *pkg, int *core, int *smt, int *nr_core);

#endif
