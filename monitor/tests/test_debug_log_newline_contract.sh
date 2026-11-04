#!/bin/sh
# Regression: every ERROR()/TRACE() format must end in "\n" (or be the "%s" pass-through).
# DEBUG ERROR/TRACE are a bare fprintf (trace.h), so a missing "\n" glues the next message on.
# c122-018: proc_filter's per-pid TRACE and the RMQ debug ERRORs had none, DEBUG output ran into
# multi-megabyte lines, and "DCGM embedded engine started" was glued to a proc_filter line that
# verify_grace_scf_imc.sh then dropped as noise.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Join adjacent string literals ("a" newline "b"), then test the last literal of the format.
bad="$(awk '
  function check(buf, file, line,   fmt) {
    if (!match(buf, /(ERROR|TRACE)\("([^"\\]|\\.)*"([ \t]*"([^"\\]|\\.)*")*/))
      return
    fmt = substr(buf, RSTART, RLENGTH)
    if (fmt ~ /\("%s"$/ || fmt ~ /\\n"$/)
      return
    printf "%s:%d: %s\n", file, line, fmt
  }
  /(^|[^A-Za-z_])(ERROR|TRACE)\("/ {
    buf = $0; line = FNR
    while (buf ~ /"[ \t]*$/ && (getline nxt) > 0) {
      sub(/^[ \t]*/, "", nxt)
      if (nxt !~ /^"/) { buf = buf nxt; break }
      buf = buf nxt
    }
    check(buf, FILENAME, line)
  }
' "${ROOT}"/src/*.c)"

if [ -n "${bad}" ]; then
  echo "ERROR/TRACE format without trailing \\n:" >&2
  echo "${bad}" >&2
  exit 1
fi

# Positive control: the matcher must see the known TRACE in proc_filter.c.
grep -qE 'TRACE\("Do not include this proc entry %s\\n"' "${ROOT}/src/proc_filter.c" \
  || { echo "proc_filter.c TRACE not found or still missing \\n" >&2; exit 1; }

echo "test_debug_log_newline_contract passed"
