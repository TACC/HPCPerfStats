/*! \file likwid_result_convert.h
 *  Convert LIKWID perfmon_getResult doubles to unsigned counters safely.
 */

#ifndef LIKWID_RESULT_CONVERT_H
#define LIKWID_RESULT_CONVERT_H

#include <stddef.h>

/* Schema W=48 upper bound used by IMC (and similar) counter keys. */
#define LIKWID_RESULT_U48_MAX ((1ULL << 48) - 1)

/*
 * Convert a LIKWID result double to ull.
 * Returns 0 on success and stores the truncated value in *out.
 * Returns -1 when out is NULL, d is non-finite, d < 0, or d > max_incl.
 * Passing max_incl == ~(unsigned long long)0 disables the upper bound.
 */
int likwid_result_to_ull(double d, unsigned long long max_incl, unsigned long long *out);

#endif /* LIKWID_RESULT_CONVERT_H */
