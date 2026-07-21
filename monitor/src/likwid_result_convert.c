/*! \file likwid_result_convert.c
 *  Safe conversion of LIKWID perfmon doubles (reject NaN → 2^63 poison).
 */

#include "likwid_result_convert.h"

#include <math.h>

int likwid_result_to_ull(double d, unsigned long long max_incl, unsigned long long *out)
{
  unsigned long long v;

  if (out == NULL)
    return -1;
  if (!isfinite(d) || d < 0.0)
    return -1;
  if (max_incl != ~(unsigned long long)0 && d > (double)max_incl)
    return -1;
  v = (unsigned long long)d;
  if (max_incl != ~(unsigned long long)0 && v > max_incl)
    return -1;
  *out = v;
  return 0;
}
