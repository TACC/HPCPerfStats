/* InfiniBand port state/phys_state sysfs line parsers (testable, no I/O). */
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#include "ib_port_state.h"

/* IB_PORT_ACTIVE == 4; sysfs may print "4: ACTIVE", "active", or "inactive". */
int ib_port_logic_active(const char *state_line)
{
  const char *p;
  char *endp = NULL;
  unsigned long v;

  if (state_line == NULL)
    return 0;
  p = state_line;
  while (*p != '\0' && isspace((unsigned char)*p))
    p++;
  v = strtoul(p, &endp, 10);
  if (endp != p && v == 4)
    return 1;
  if (strstr(state_line, "inactive") != NULL)
    return 0;
  if (strstr(state_line, "active") != NULL)
    return 1;
  return 0;
}

/* IB_LINK_LAYER_ACTIVE / LinkUp wording varies by kernel. */
int ib_port_phys_link_up(const char *phys_line)
{
  const char *p;
  char *endp = NULL;
  unsigned long v;

  if (phys_line == NULL)
    return 0;
  p = phys_line;
  while (*p != '\0' && isspace((unsigned char)*p))
    p++;
  v = strtoul(p, &endp, 10);
  if (endp != p && v == 5)
    return 1;
  if (strstr(phys_line, "link_up") != NULL || strstr(phys_line, "linkup") != NULL)
    return 1;
  return 0;
}
