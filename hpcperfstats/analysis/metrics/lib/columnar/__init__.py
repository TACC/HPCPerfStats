"""
Columnar (pandas-free) update_metrics path: JobFrame, COPY fetch and kernels.

One ``COPY … (FORMAT BINARY)`` fetch per job becomes a sorted structure of
arrays (:class:`~hpcperfstats.analysis.metrics.lib.columnar.jobframe.JobFrame`)
that the metrics (K1), Job Detail (K2) and plot-series (K3) kernels read.
Names (host, type, event, dev) stay in CPython as small id tables; kernels
only see integer ids and float columns.
"""
