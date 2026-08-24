/* DCGM CPU backend: cpu_counter_metrics_likwid_ready must stay declared+defined
 * (stub returns 0). Locks the Horizon aarch64 rpmbuild failure where uncore/RAPL
 * call ready under HAVE_LIKWID but the prototype was gated out of the header.
 *
 * Built with -DMONITOR_CPU_BACKEND_DCGM -Werror=implicit-function-declaration.
 */
#include <assert.h>
#include <stdio.h>

#include "cpu_counter_metrics_likwid_begin.h"

static void test_ready_stub_returns_zero(void)
{
  assert(cpu_counter_metrics_likwid_ready() == 0);
}

int main(void)
{
  test_ready_stub_returns_zero();
  puts("test_cpu_counter_metrics_likwid_ready_dcgm: OK");
  return 0;
}
