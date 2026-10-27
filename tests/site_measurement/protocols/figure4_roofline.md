# Figure 4 (GPU roofline) evidence checklist (AUTHOR_NOTES item 8)

Do **not** claim the plotted job is unoptimized because of system
misbehavior, application structure, or a mistuned roof unless you have
that evidence. The current paper treats Figure 4 as a **capability
screenshot**, not as the hung-job diagnosis.

## Required before any causal sentence

Check each box with a fact (job id, metric, or “unknown”):

1. [ ] Job id and system for GPURoofline.png
2. [ ] Whether Job Detail shows GPU throttle (`clocks_event_reasons`) or power/temp limits
3. [ ] Whether fabric / I/O stalls appear on the same job (IOPS, IB)
4. [ ] Whether the roof used in the plot matches the SKU (measured peaks vs table)
5. [ ] Whether the application is known to be bandwidth- or compute-bound from an independent profiler (Nsight, rocprof) — optional, only if actually run
6. [ ] Hung-job diagnosis for jobs 2688 / 2699 uses I/O, fabric, NUMA, GPU-link tabs — **not** this roofline figure

If any of 2–5 is unknown, keep the paper wording: capability screenshot, not diagnosis.
