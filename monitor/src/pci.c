#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <dirent.h>
#include <limits.h>
#include "trace.h"
#include "path_open_fail_once.h"

#define PCI_DIR_PATH "/proc/bus/pci"

int pci_map_create(char ***dev_paths, int *nr, int *ids, int nr_ids) { 

  int rc = -1;
  *nr = 0;
  
  const char *pci_dir_path = PCI_DIR_PATH;
  DIR *pci_dir = NULL;
  
  pci_dir = path_opendir_or_record_fail(pci_dir_path);
  if (pci_dir == NULL)
    goto out;
  
  struct dirent *bus_no;
  while ((bus_no = readdir(pci_dir)) != NULL) {
    if (bus_no->d_type != DT_DIR || *bus_no->d_name == '.')
      continue;

    char bus_dir_path[PATH_MAX];
    if (snprintf(bus_dir_path, sizeof(bus_dir_path), "%s/%s",
		 pci_dir_path, bus_no->d_name) >= (int)sizeof(bus_dir_path))
      continue;

    DIR *bus_dir = NULL;
    bus_dir = path_opendir_or_record_fail(bus_dir_path);
    if (bus_dir == NULL)
      continue;

    struct dirent *dev_fun_no;
    while ((dev_fun_no = readdir(bus_dir)) != NULL) {
      if (dev_fun_no->d_type == DT_DIR)
	continue;
      
      int pci_fd = -1;
      char dev_fun_path[PATH_MAX];
      if (snprintf(dev_fun_path, sizeof(dev_fun_path), "%s/%s/%s",
		   pci_dir_path, bus_no->d_name, dev_fun_no->d_name) >= (int)sizeof(dev_fun_path))
	continue;
      
      if (path_open_is_skipped(dev_fun_path))
	continue;
      pci_fd = open(dev_fun_path, O_RDONLY);
      if (pci_fd < 0) {
	path_open_record_failure_once(dev_fun_path);
	continue;
      }

      uint16_t reg[2];
      if (pread(pci_fd, &reg, sizeof(reg), 0) < 0) {
	ERROR("cannot read device vendor or id from %s\n", dev_fun_path);
	goto next;
      }
      if (reg[0] != 0x8086) 
	goto next;

      int i;
      for (i = 0; i < nr_ids; i++)
	if (ids[i] == reg[1]) { 
	  char **tmp_dev_paths = (char **) realloc(*dev_paths, (size_t)(*nr + 1) * sizeof(char *));
	  char *slot;

	  if (tmp_dev_paths == NULL) {
	    TRACE("dev path realloc failed\n");
	    continue;
	  }
	  *dev_paths = tmp_dev_paths;
	  slot = (char *) malloc(strlen(dev_fun_path) + 1);
	  if (slot == NULL) {
	    TRACE("dev path not allocated\n");
	    continue;
	  }
	  (*dev_paths)[*nr] = slot;

	  {
	    int wrote = snprintf((*dev_paths)[*nr], strlen(dev_fun_path) + 1, "%s/%s",
				 bus_no->d_name, dev_fun_no->d_name);
	    if (wrote < 0 || (size_t)wrote >= strlen(dev_fun_path) + 1) {
	      free((*dev_paths)[*nr]);
	      (*dev_paths)[*nr] = NULL;
	      continue;
	    }
	  }
	  TRACE("%x %x %s\n", reg[0], reg[1], (*dev_paths)[*nr]);
	  (*nr)++;
	}

    next:
      if (pci_fd > 0)
	close(pci_fd);

    }

    if (bus_dir != NULL)
      closedir(bus_dir);
  }

  if (pci_dir != NULL)
    closedir(pci_dir);

  rc = 0;

 out:
  return rc;
}

void pci_map_destroy(char ***dev_paths, int nr_ids) {

  if (*dev_paths != NULL) {
    int i;
    for (i=0; i < nr_ids; i++) {
      if ((*dev_paths)[i] != NULL)
	free((*dev_paths)[i]);
    }
    free(*dev_paths); 
  }

}
