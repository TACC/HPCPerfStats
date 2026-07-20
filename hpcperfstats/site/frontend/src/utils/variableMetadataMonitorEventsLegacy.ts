/**
 * Legacy monitor event keys (historical host_data only). Canonical keys live in
 * variableMetadataMonitorEvents.ts. Do not add new entries here.
 */
export const MONITOR_EVENT_METADATA_LEGACY = {
  CTL0: { description: "Performance event select register (programs the paired general-purpose counter)." },
  CTL1: { description: "Performance event select register (programs the paired general-purpose counter)." },
  CTL2: { description: "Performance event select register (programs the paired general-purpose counter)." },
  CTL3: { description: "Performance event select register (programs the paired general-purpose counter)." },
  CTL4: { description: "Performance event select register (programs the paired general-purpose counter)." },
  CTL5: { description: "Performance event select register (programs the paired general-purpose counter)." },
  CTL6: { description: "Performance event select register (programs the paired general-purpose counter)." },
  CTL7: { description: "Performance event select register (programs the paired general-purpose counter)." },
  CTR0: { description: "General-purpose performance-monitoring counter value; meaning depends on paired CTL programming." },
  CTR1: { description: "General-purpose performance-monitoring counter value; meaning depends on paired CTL programming." },
  CTR2: { description: "General-purpose performance-monitoring counter value; meaning depends on paired CTL programming." },
  CTR3: { description: "General-purpose performance-monitoring counter value; meaning depends on paired CTL programming." },
  CTR4: { description: "General-purpose performance-monitoring counter value; meaning depends on paired CTL programming." },
  CTR5: { description: "General-purpose performance-monitoring counter value; meaning depends on paired CTL programming." },
  CTR6: { description: "General-purpose performance-monitoring counter value; meaning depends on paired CTL programming." },
  CTR7: { description: "General-purpose performance-monitoring counter value; meaning depends on paired CTL programming." },
  FIXED_CTR0: { description: "Intel fixed counter 0 (typically instructions retired)." },
  FIXED_CTR1: { description: "Intel fixed counter 1 (typically unhalted core cycles)." },
  FIXED_CTR2: { description: "Intel fixed counter 2 (typically reference cycles)." },
  CAS_READS: { description: "DRAM CAS read operations (legacy IMC event name)." },
  CAS_WRITES: { description: "DRAM CAS write operations (legacy IMC event name)." },
  INST_RETIRED: { description: "Instructions retired (legacy PMC event name)." },
  APERF: { description: "Actual performance frequency clock count (legacy PMC event name)." },
  MPERF: { description: "Maximum performance frequency clock count (legacy PMC event name)." },
  FLOPS: { description: "Floating-point operations retired (legacy AMD PMC aggregate)." },
  MemTotal: { description: "Total system memory from /proc/meminfo (legacy key)." },
  MemUsed: { description: "Used system memory (legacy key)." },
  MemFree: { description: "Free system memory (legacy key)." },
};
