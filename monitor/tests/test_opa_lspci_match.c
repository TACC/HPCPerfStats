/* Stampede3 OPA/Cornelis lspci fixture lines match hwdetect OPA predicates. */
#include <assert.h>
#include <ctype.h>
#include <stdio.h>
#include <string.h>

#ifndef OPA_LSPCI_FIXTURE
#error OPA_LSPCI_FIXTURE required
#endif

static void to_lower_ascii(char *s)
{
  while (*s != '\0') {
    *s = (char)tolower((unsigned char)*s);
    s++;
  }
}

static int line_matches_opa(const char *line)
{
  return strstr(line, "omnipath") != NULL || strstr(line, "hfi") != NULL ||
         strstr(line, "cornelis") != NULL || strstr(line, "cn5000") != NULL;
}

static void test_fixture_lines(void)
{
  FILE *fp = fopen(OPA_LSPCI_FIXTURE, "r");
  char line[1024];
  int n = 0;

  assert(fp != NULL);
  while (fgets(line, sizeof(line), fp) != NULL) {
    char *tab;
    if (line[0] == '#' || line[0] == '\n')
      continue;
    tab = strchr(line, '\t');
    if (tab == NULL)
      continue;
    to_lower_ascii(tab + 1);
    assert(line_matches_opa(tab + 1));
    n++;
  }
  fclose(fp);
  assert(n >= 2);
}

int main(void)
{
  test_fixture_lines();
  printf("test_opa_lspci_match passed\n");
  return 0;
}
