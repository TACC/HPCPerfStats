/* ib_hca_is_opa_hfi — HFI vs IB HCA classification; opa_hfi_unit_from_name. */
#include <assert.h>
#include <stdio.h>

#include "ib_common.h"

static void test_hfi1_true(void)
{
  assert(ib_hca_is_opa_hfi("hfi1_0") == 1);
  assert(ib_hca_is_opa_hfi("hfi1_1") == 1);
  assert(ib_hca_is_opa_hfi("hfi1") == 1);
}

static void test_ib_hcas_false(void)
{
  assert(ib_hca_is_opa_hfi("mlx5_0") == 0);
  assert(ib_hca_is_opa_hfi("mlx4_0") == 0);
  assert(ib_hca_is_opa_hfi("qib0") == 0);
  assert(ib_hca_is_opa_hfi("hfi10") == 0);
  assert(ib_hca_is_opa_hfi("xhfi1_0") == 0);
}

static void test_null_empty(void)
{
  assert(ib_hca_is_opa_hfi(NULL) == 0);
  assert(ib_hca_is_opa_hfi("") == 0);
}

static void test_opa_hfi_unit_from_name(void)
{
  assert(opa_hfi_unit_from_name("hfi1_0") == 0);
  assert(opa_hfi_unit_from_name("hfi1_1") == 1);
  assert(opa_hfi_unit_from_name("hfi1_12") == 12);
  assert(opa_hfi_unit_from_name("hfi1") == 0);
  assert(opa_hfi_unit_from_name("mlx5_0") == -1);
  assert(opa_hfi_unit_from_name("hfi1_") == -1);
  assert(opa_hfi_unit_from_name("hfi1_0x") == -1);
  assert(opa_hfi_unit_from_name(NULL) == -1);
  assert(opa_hfi_unit_from_name("") == -1);
}

int main(void)
{
  test_hfi1_true();
  test_ib_hcas_false();
  test_null_empty();
  test_opa_hfi_unit_from_name();
  printf("test_ib_hca_is_opa_hfi passed\n");
  return 0;
}
