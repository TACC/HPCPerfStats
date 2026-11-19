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
  SKYLAKE_X,    /* server 06_55 stepping < 5 */
  CASCADE_LAKE, /* server 06_55 stepping >= 5 (also Cooper Lake) */
  ICELAKE_SERVER,
  SAPPHIRE_RAPIDS,
  EMERALD_RAPIDS,
  GRANITE_RAPIDS,
  SIERRA_FOREST,
  ARM_GRACE /* Neoverse V2 / Grace */
} processor_t;

processor_t signature(int *n_pmcs);

#endif
