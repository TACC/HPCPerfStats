#ifndef CPU_COUNTER_METRICS_DCGM_PUBLISH_H_
#define CPU_COUNTER_METRICS_DCGM_PUBLISH_H_
#ifdef MONITOR_CPU_BACKEND_DCGM
struct stats;

struct dcgm_cpu_sample {
  double util_total;
  double util_user;
  double util_nice;
  double util_sys;
  double util_irq;
  double clock_khz;
  long long ts;
};

void dcgm_accumulate_from_util_sample(int i, struct dcgm_cpu_sample *sample, long long delta_us);
void publish_dcgm_cpu_stats(struct stats *stats, int i);
#endif
#endif
