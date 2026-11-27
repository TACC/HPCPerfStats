"""Synthetic E6 feed_line corpus (shared by parity tests and benchmarks)."""

_E6_SCHEMA_LINES = [
    "1709123456 job1 cn001\n",
    "!cpu user,W=48 sys,W=48 idle,W=48\n",
    "!host_proc vm_peak,U=kB rss_peak,U=kB threads\n",
]


def build_e6_payload_lines(n_samples=400):
  """Build a mid-size feed_line corpus (schema once, then timed samples)."""
  lines = list(_E6_SCHEMA_LINES)
  t0 = 1_709_123_456
  for i in range(n_samples):
    t = t0 + i
    lines.append(str(t) + " job1 cn001\n")
    lines.append(
        "cpu 0 @full " + str(10 + i) + " " + str(20 + i) + " " + str(30 + i) + "\n",
    )
    lines.append(
        "host_proc bash/1/0/0 @full "
        + str(100 + i)
        + " "
        + str(50 + i)
        + " "
        + str(4 + (i % 8))
        + "\n",
    )
  return lines


HOST_PROC_AT_FULL_FIXTURE = [
    (
        "!host_proc uid,R=S vm_peak,U=kB vm_size,U=kB vm_lck,U=kB,R=S "
        "vm_hwm,U=kB,R=S vm_rss,U=kB vm_data,U=kB vm_stk,U=kB vm_exe,U=kB "
        "vm_lib,U=kB vm_pte,U=kB,R=S vm_swap,U=kB threads\n"
    ),
    "1709123456 job1 cn001\n",
    (
        "host_proc polkitd/499505/0-143/0-2,10,18,26 @full "
        "114 374080 308544 0 9920 9920 35200 192 128 9920 448 0 4\n"
    ),
]
