/* Unit tests for ib_port_state sysfs line parsers (pure logic, no I/O). */
#include <assert.h>
#include <stdio.h>

#include "ib_port_state.h"

static void test_ib_port_logic_active_cases(void)
{
  /* Arrange / Act / Assert: numeric ACTIVE (4). */
  assert(ib_port_logic_active("4") == 1);
  assert(ib_port_logic_active("4: ACTIVE") == 1);

  assert(ib_port_logic_active("inactive") == 0);
  /* Numeric prefix 4 (ACTIVE) wins over trailing INACTIVE text. */
  assert(ib_port_logic_active("4: INACTIVE") == 1);

  assert(ib_port_logic_active(NULL) == 0);
  assert(ib_port_logic_active("3: DOWN") == 0);
  assert(ib_port_logic_active("") == 0);
}

static void test_ib_port_phys_link_up_cases(void)
{
  assert(ib_port_phys_link_up("5") == 1);
  /* Case-sensitive substring match: "LinkUp" is not "linkup". */
  assert(ib_port_phys_link_up("LinkUp") == 0);
  assert(ib_port_phys_link_up("5: LinkUp") == 1);
  assert(ib_port_phys_link_up("linkup") == 1);
  assert(ib_port_phys_link_up("2: Polling") == 0);

  assert(ib_port_phys_link_up(NULL) == 0);
  assert(ib_port_phys_link_up("") == 0);
}

int main(void)
{
  test_ib_port_logic_active_cases();
  test_ib_port_phys_link_up_cases();
  printf("test_ib_port_state passed\n");
  return 0;
}
