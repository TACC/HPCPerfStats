#include <assert.h>
#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include "gpu_pci_detect.h"

static void to_lower_ascii(char *s)
{
  while (*s != '\0') {
    *s = (char) tolower((unsigned char) *s);
    s++;
  }
}

static void test_fixture_file(void)
{
#ifdef GPU_PCI_DETECT_FIXTURE
  const char *fixture = GPU_PCI_DETECT_FIXTURE;
#else
  const char *fixture = "fixtures/gpu_lspci_lines.tsv";
#endif
  FILE *fp = fopen(fixture, "r");
  char line[1024];
  char lspci_line[1024];
  int expect_nvidia;
  int expect_amd;
  int expect_intel;
  int n = 0;

  assert(fp != NULL);

  while (fgets(line, sizeof(line), fp) != NULL) {
    if (line[0] == '#' || line[0] == '\n')
      continue;
    if (sscanf(line, "%[^\t]\t%d\t%d\t%d", lspci_line, &expect_nvidia, &expect_amd,
               &expect_intel)
        != 4)
      continue;

    to_lower_ascii(lspci_line);
    assert((gpu_pci_line_indicates_nvidia(lspci_line) ? 1 : 0) == expect_nvidia);
    assert((gpu_pci_line_indicates_amd(lspci_line) ? 1 : 0) == expect_amd);
    assert((gpu_pci_line_indicates_intel_datacenter_gpu(lspci_line) ? 1 : 0)
           == expect_intel);
    n++;
  }
  fclose(fp);
  assert(n >= 10);
}

int main(void)
{
  test_fixture_file();
  printf("test_gpu_pci_detect passed\n");
  return 0;
}
