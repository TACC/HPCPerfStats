## @file intel_process.py
# Post-processing for core and uncore NHM/WTM/SNB events
# are performed in this file for the pickler.
#
# The registers must be reported in the following order in the stats file:
# -# CTL registers
# -# Programmable CTR registers
# -# Fixed CTR registers
# -# If CTL register programming failed it (QPI) it seems to be 0, so this is added to event maps
# ---->>>> Programmable CTL registers must be in same order as CTR
"""Intel processor and uncore event encoding (CORE_PERF_EVENT, CBOX, HAU, IMC, QPI, etc.) and reformat_counters/format_knl/process_job for mapping raw counters to event names.

"""
import numpy

from hpcperfstats.print_utils import log_print


## Processor events
def CORE_PERF_EVENT(event_select, unit_mask):
  """Encode Intel core PERF_EVENT (no any-thread bit).

    """
  return event_select | (unit_mask << 8) | (1 << 16) | (1 << 17) | (0 << 21) | (
      1 << 22)


# bit 21 is any thread (every hardware thread on a core increments on event)
def CORE_PERF_EVENT1(event_select, unit_mask):
  """Encode Intel core PERF_EVENT with any-thread bit set.

    """
  return event_select | (unit_mask << 8) | (1 << 16) | (1 << 17) | (1 << 21) | (
      1 << 22)


## Processor event map
cpu_event_map = {
    CORE_PERF_EVENT(0xC7, 0x01): 'FP_ARITH_INST_RETIRED_SCALAR_DOUBLE,E',
    CORE_PERF_EVENT(0xC7, 0x02): 'FP_ARITH_INST_RETIRED_SCALAR_SINGLE,E',
    CORE_PERF_EVENT(0xC7, 0x04): 'FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE,E',
    CORE_PERF_EVENT(0xC7, 0x08): 'FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE,E',
    CORE_PERF_EVENT(0xC7, 0x10): 'FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE,E',
    CORE_PERF_EVENT(0xC7, 0x20): 'FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE,E',
    CORE_PERF_EVENT(0xC7, 0x40): 'FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE,E',
    CORE_PERF_EVENT(0xC7, 0x80): 'FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE,E',
    CORE_PERF_EVENT(0xD0, 0x81): 'LOAD_OPS_ALL,E',
    CORE_PERF_EVENT(0xD1, 0x01): 'LOAD_OPS_L1_HIT,E',
    CORE_PERF_EVENT(0xD1, 0x02): 'LOAD_OPS_L2_HIT,E',
    CORE_PERF_EVENT(0xD1, 0x04): 'LOAD_OPS_LLC_HIT,E',
    CORE_PERF_EVENT(0xD1, 0x20): 'MEM_LOAD_UOPS_RETIRED_LLC_MISS,E',
    CORE_PERF_EVENT(0xD1, 0x40): 'MEM_LOAD_UOPS_RETIRED_HIT_LFB,E',
    CORE_PERF_EVENT(0xD1, 0x08): 'MEM_LOAD_UOPS_RETIRED_L1_MISS,E',
    CORE_PERF_EVENT(0xD1, 0x10): 'MEM_LOAD_UOPS_RETIRED_L2_MISS,E',
    CORE_PERF_EVENT(0xD1, 0x20): 'MEM_LOAD_UOPS_RETIRED_L3_MISS,E',
    CORE_PERF_EVENT(0x10, 0x90): 'SSE_DOUBLE_ALL,E',
    CORE_PERF_EVENT(0x11, 0x02): 'SIMD_DOUBLE_256,E',
    CORE_PERF_EVENT(0xA2, 0x01): 'STALLS,E',
    CORE_PERF_EVENT(0x51, 0x01): 'LOAD_L1D_ALL,E',
    CORE_PERF_EVENT(0x10, 0x80): 'SSE_DOUBLE_SCALAR,E',
    CORE_PERF_EVENT(0x10, 0x10): 'SSE_DOUBLE_PACKED,E',
    CORE_PERF_EVENT(0xF1, 0x07): 'L2_LINES_IN_ALL,E',
    CORE_PERF_EVENT1(0xD0, 0x81): 'LOAD_OPS_ALL,E',
    CORE_PERF_EVENT1(0xD1, 0x01): 'LOAD_OPS_L1_HIT,E',
    CORE_PERF_EVENT1(0xD1, 0x02): 'LOAD_OPS_L2_HIT,E',
    CORE_PERF_EVENT1(0xD1, 0x04): 'LOAD_OPS_LLC_HIT,E',
    CORE_PERF_EVENT1(0xD1, 0x20): 'MEM_LOAD_UOPS_RETIRED_LLC_MISS,E',
    CORE_PERF_EVENT1(0xD1, 0x40): 'MEM_LOAD_UOPS_RETIRED_HIT_LFB,E',
    CORE_PERF_EVENT1(0xD1, 0x08): 'MEM_LOAD_UOPS_RETIRED_L1_MISS,E',
    CORE_PERF_EVENT1(0xD1, 0x10): 'MEM_LOAD_UOPS_RETIRED_L2_MISS,E',
    CORE_PERF_EVENT1(0xD1, 0x20): 'MEM_LOAD_UOPS_RETIRED_L3_MISS,E',
    CORE_PERF_EVENT1(0x10, 0x90): 'SSE_DOUBLE_ALL,E',
    CORE_PERF_EVENT1(0x11, 0x02): 'SIMD_DOUBLE_256,E',
    CORE_PERF_EVENT1(0xA2, 0x01): 'STALLS,E',
    CORE_PERF_EVENT1(0x51, 0x01): 'LOAD_L1D_ALL,E',
    CORE_PERF_EVENT1(0x10, 0x80): 'SSE_DOUBLE_SCALAR,E',
    CORE_PERF_EVENT1(0x10, 0x10): 'SSE_DOUBLE_PACKED,E',
    CORE_PERF_EVENT1(0xF1, 0x07): 'L2_LINES_IN_ALL,E',
    CORE_PERF_EVENT(0x04, 0x40): 'MEM_UOPS_RETIRED_ALL_LOADS',
    CORE_PERF_EVENT(0x04, 0x02): 'MEM_UOPS_RETIRED_L2_HIT_LOADS',
    CORE_PERF_EVENT1(0x04, 0x40): 'MEM_UOPS_RETIRED_ALL_LOADS',
    CORE_PERF_EVENT1(0x04, 0x02): 'MEM_UOPS_RETIRED_L2_HIT_LOADS',
    'FIXED0': 'INSTRUCTIONS_RETIRED,E',
    'FIXED1': 'CLOCKS_UNHALTED_CORE,E',
    'FIXED2': 'CLOCKS_UNHALTED_REF,E',
}


## CBo events
def CBOX_PERF_EVENT(event, umask):
  """Encode Intel CBo (cache box) PERF_EVENT from event select and unit mask."""
  return (event) | (umask << 8) | (0 << 17) | (0 << 18) | (0 << 19) | (
      1 << 22) | (0 << 23) | (1 << 24)


## CBo event map
cbo_event_map = {
    CBOX_PERF_EVENT(0x00, 0x00): 'CLOCK_TICKS,E',
    CBOX_PERF_EVENT(0x11, 0x01): 'RxR_OCCUPANCY,E',
    CBOX_PERF_EVENT(0x1F, 0x00): 'COUNTER0_OCCUPANCY,E',
    CBOX_PERF_EVENT(0x34, 0x03): 'LLC_LOOKUP_DATA_READ,E',
    CBOX_PERF_EVENT(0x34, 0x11): 'LLC_LOOKUP_ANY,E',
    CBOX_PERF_EVENT(0x34, 0x05): 'LLC_LOOKUP_WRITE,E',
    CBOX_PERF_EVENT(0x1E, 0x0F): 'RING_IV_USED,E'
}


## Home Agent Unit events
def HAU_PERF_EVENT(event, umask):
  """Encode Intel Home Agent Unit PERF_EVENT from event select and unit mask."""
  return (event) | (umask << 8) | (0 << 18) | (1 << 22) | (0 << 23) | (1 << 24)


## Home Agent map
hau_event_map = {
    HAU_PERF_EVENT(0x01, 0x03): 'READ_REQUESTS,E',
    HAU_PERF_EVENT(0x01, 0x0C): 'WRITE_REQUESTS,E',
    HAU_PERF_EVENT(0x00, 0x00): 'CLOCKTICKS,E',
    HAU_PERF_EVENT(0x1A, 0x0F): 'IMC_WRITES,E',
}


## Integrated Memory events
def IMC_PERF_EVENT(event, umask):
  """Encode Intel Integrated Memory Controller PERF_EVENT from event select and unit mask."""
  return (event) | (umask << 8) | (0 << 18) | (1 << 22) | (0 << 23) | (1 << 24)


def IMC_PERF_EVENT_SKX(event, umask):
  """Encode Intel SKX IMC PERF_EVENT from event select and unit mask (variant encoding)."""
  return (event) | (umask << 8) | (0 << 18) | (1 << 22) | (0 << 23) | (0 << 24)


## Integrated Memory Controller map
imc_event_map = {
    IMC_PERF_EVENT(0x04, 0x03): 'CAS_READS,E',
    IMC_PERF_EVENT(0x04, 0x0C): 'CAS_WRITES,E',
    IMC_PERF_EVENT(0x01, 0x00): 'ACT_COUNT,E',
    IMC_PERF_EVENT(0x01, 0x11): 'ACT_COUNT,E',
    IMC_PERF_EVENT_SKX(0x01, 0x0B): 'ACT_COUNT,E',
    IMC_PERF_EVENT_SKX(0x04, 0x03): 'CAS_READS,E',
    IMC_PERF_EVENT_SKX(0x04, 0x0C): 'CAS_WRITES,E',
    IMC_PERF_EVENT_SKX(0x02, 0x01): 'PRE_COUNT_MISS,E',
    IMC_PERF_EVENT(0x02, 0x03): 'PRE_COUNT_ALL,E',
    IMC_PERF_EVENT(0x02, 0x01): 'PRE_COUNT_MISS,E',
    'FIXED0': 'CYCLES,E',
}

## Caching Home Agent Map for SKX
cha_event_map = {
    0x0040073d: "SF_EVICTIONS_MES,E",
    0x00403334: "LLC_LOOKUP_DATA_READ_LOCAL,E",
    0x00400757: "BYPASS_CHA_IMC_ALL,E",
    0x00400534: "LLC_LOOKUP_WRITE"
}


## Power Control Unit events
def PCU_PERF_EVENT(event):
  """Encode Intel Power Control Unit PERF_EVENT from event select."""
  return (event) | (0 << 14) | (0 << 17) | (0 << 18) | (1 << 22) | (0 << 23) | (
      1 << 24) | (0 << 31)


## Power Control map
pcu_event_map = {
    PCU_PERF_EVENT(0x06): 'MAX_OS_CYCLES,E',
    PCU_PERF_EVENT(0x07): 'MAX_CURRENT_CYCLES,E',
    PCU_PERF_EVENT(0x04): 'MAX_TEMP_CYCLES,E',
    PCU_PERF_EVENT(0x05): 'MAX_POWER_CYCLES,E',
    PCU_PERF_EVENT(0x81): 'MIN_IO_CYCLES,E',
    PCU_PERF_EVENT(0x82): 'MIN_SNOOP_CYCLES,E',
    'FIXED0': 'C3_CYCLES,E',
    'FIXED1': 'C6_CYCLES,E',
}


## QPI Unit events
def QPI_PERF_EVENT(event, umask):
  """Encode Intel QPI (QuickPath) PERF_EVENT from event select and unit mask."""
  return (event) | (umask << 8) | (0 << 18) | (1 << 21) | (1 << 22) | (0 << 23)


## QPI map
qpi_event_map = {
    QPI_PERF_EVENT(0x00, 0x01): 'TxL_FLITS_G1_SNP,E',
    QPI_PERF_EVENT(0x00, 0x04): 'TxL_FLITS_G1_HOM,E',
    QPI_PERF_EVENT(0x02, 0x08): 'G1_DRS_DATA,E',
    QPI_PERF_EVENT(0x03, 0x04): 'G2_NCB_DATA,E',
}


## R2PCI Unit events
def R2PCI_PERF_EVENT(event, umask):
  """Encode Intel R2PCI PERF_EVENT from event select and unit mask."""
  return (event) | (umask << 8) | (0 << 18) | (1 << 22) | (0 << 23) | (1 << 24)


## R2PCI map
r2pci_event_map = {
    R2PCI_PERF_EVENT(0x24, 0x04): 'TRANSMITS,E',
    R2PCI_PERF_EVENT(0x01, 0x00): 'CLOCKTICKS,E',
    R2PCI_PERF_EVENT(0x07, 0x0F): 'ADDRESS_USED,E',
    R2PCI_PERF_EVENT(0x08, 0x0F): 'ACKNOWLEDGED_USED,E',
    R2PCI_PERF_EVENT(0x09, 0x0F): 'DATA_USED,E',
}


## WESTMERE events
def WTM_PERF_EVENT(event, umask):
  """Encode Intel Westmere core PERF_EVENT from event select and unit mask."""
  return (event) | (umask << 8) | (1 << 16) | (1 << 17) | (1 << 21) | (1 << 22)


## WTM map
wtm_event_map = {
    WTM_PERF_EVENT(0x0F, 0x10): 'MEM_UNCORE_RETIRED_REMOTE_DRAM,E',
    WTM_PERF_EVENT(0x0F, 0x20): 'MEM_UNCORE_RETIRED_LOCAL_DRAM,E',
    WTM_PERF_EVENT(0x10, 0x01): 'FP_COMP_OPS_EXE_X87,E',
    WTM_PERF_EVENT(0x10, 0x10): 'FP_COMP_OPS_EXE_SSE_PACKED,E',
    WTM_PERF_EVENT(0x10, 0x20): 'FP_COMP_OPS_EXE_SSE_SCALAR,E',
    WTM_PERF_EVENT(0xCB, 0x01): 'MEM_LOAD_RETIRED_L1D_HIT,E',
    'FIXED0': 'INSTRUCTIONS_RETIRED,E',
    'FIXED1': 'CLOCKS_UNHALTED_CORE,E',
    'FIXED2': 'CLOCKS_UNHALTED_REF,E',
}


## WESTMERE UNCORE events
def WTMUNC_PERF_EVENT(event, umask):
  """Encode Intel Westmere uncore PERF_EVENT from event select and unit mask."""
  return (event) | (umask << 8) | (1 << 22)


## WTM map
wtmunc_event_map = {
    WTMUNC_PERF_EVENT(0x08, 0x01): 'L3_HITS_READ,E',
    WTMUNC_PERF_EVENT(0x08, 0x02): 'L3_HITS_WRITE,E',
    WTMUNC_PERF_EVENT(0x08, 0x04): 'L3_HITS_PROBE,E',
    WTMUNC_PERF_EVENT(0x09, 0x01): 'L3_MISS_READ,E',
    WTMUNC_PERF_EVENT(0x09, 0x02): 'L3_MISS_WRITE,E',
    WTMUNC_PERF_EVENT(0x09, 0x04): 'L3_MISS_PROBE,E',
    WTMUNC_PERF_EVENT(0x0A, 0x0F): 'L3_LINES_IN_ANY,E',
    WTMUNC_PERF_EVENT(0x0B, 0x1F): 'L3_LINES_OUT_ANY,E',
    'FIXED0': 'CLOCKS_UNCORE,E',
}


## KNL EDC UCLK events (func 0 dev 0x0f, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16)
def KNL_EDC_UCLK_PERF_EVENT(event, umask):
  """Encode KNL EDC UCLK PERF_EVENT from event select and unit mask."""
  return (event) | (umask << 8) | (1 << 22)


knl_edc_uclk_event_map = {
    KNL_EDC_UCLK_PERF_EVENT(0x02, 0x01): 'EDC_HIT_CLEAN,E',
    KNL_EDC_UCLK_PERF_EVENT(0x02, 0x02): 'EDC_HIT_DIRTY,E',
    KNL_EDC_UCLK_PERF_EVENT(0x02, 0x04): 'EDC_MISS_CLEAN,E',
    KNL_EDC_UCLK_PERF_EVENT(0x02, 0x08): 'EDC_MISS_DIRTY,E',
}


## KNL EDC ECLK events (func 2 dev 0x18, 0x19, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e, 0x1f)
def KNL_EDC_ECLK_PERF_EVENT(event, umask):
  """Encode KNL EDC ECLK PERF_EVENT from event select and unit mask."""
  return (event) | (umask << 8) | (1 << 22)


knl_edc_eclk_event_map = {
    KNL_EDC_ECLK_PERF_EVENT(0x01, 0x01): 'RPQ_INSERTS,E',
    KNL_EDC_ECLK_PERF_EVENT(0x02, 0x01): 'WPQ_INSERTS,E',
    KNL_EDC_ECLK_PERF_EVENT(0x00, 0x00): 'ECLK_CYCLES,E'
}


## KNL MC UCLK events (func 0 dev 0x0a, 0x0b)
def KNL_MC_UCLK_PERF_EVENT(event, umask):
  """Encode KNL MC UCLK PERF_EVENT from event select and unit mask."""
  return (event) | (umask << 8) | (1 << 22)


knl_mc_uclk_event_map = {
    KNL_MC_UCLK_PERF_EVENT(0x00, 0x00): 'UCLK_CYCLES,E',
}


## KNL MC DCLK events (func 2, 3, 4 dev 0x08, 0x09)
def KNL_MC_DCLK_PERF_EVENT(event, umask):
  """Encode KNL MC DCLK PERF_EVENT from event select and unit mask."""
  return (event) | (umask << 8) | (1 << 22)


knl_mc_dclk_event_map = {
    KNL_MC_DCLK_PERF_EVENT(0x03, 0x01): 'CAS_READS,E',
    KNL_MC_DCLK_PERF_EVENT(0x03, 0x02): 'CAS_WRITES,E',
    KNL_MC_DCLK_PERF_EVENT(0x00, 0x00): 'DCLK_CYCLES,E'
}


## Reformats the counter arrays with configurable events
class reformat_counters:
  """Reformat raw counter arrays to named events using event_map; update job schema and host stats.

    """

  ## Constructor
  def __init__(self, job, name, event_map):
    """Build CTL/CTR/FIXED register lists and new schema from event_map; replace job schema for name.

        """
    ## Job Object
    self.job = job
    ## Name of device
    self.name = name
    ## List of CTL registers
    self.ctl_registers = []
    ## List of CTR registers
    self.ctr_registers = []
    ## List of Fixed registers
    self.fix_registers = []

    # Just need the first hosts schema
    stats = None
    for host in job.hosts.values():
      if name not in host.stats:
        return
      stats = host.stats[name]
      break

    if stats == None:
      return

    schema_desc = job.schemas.get(self.name).desc
    registers = schema_desc.split()

    for reg in registers:
      if reg.split(',')[1] != 'C':
        if reg.find('FIXED') == -1:
          self.ctr_registers.append(registers.index(reg))
        else:
          self.fix_registers.append(registers.index(reg))
      else:
        if reg.find('GLOBAL') == -1 and reg.find('FIXED') == -1 and reg.find(
            'OPCODE_MATCH') == -1:
          self.ctl_registers.append(registers.index(reg))

    # Build Schema from ctl registers and event maps
    dev_schema = []
    for dev, array in stats.items():
      for j in self.ctl_registers:
        dev_schema.append(event_map.get(array[0, j], str(array[0, j])))
      break

    # Now check for all hosts:
    # all devices have the same control settings
    # all devices control setting remain the same during the job
    for host in job.hosts.values():
      if name in host.stats:
        for dev, array in host.stats[name].items():
          devidx = 0
          for j in self.ctl_registers:
            settings = array[:, j]
            if event_map.get(settings[0], str(
                settings[0])) != dev_schema[devidx] or settings.min(
                ) != settings.max():
              # mark as the error metric
              dev_schema[devidx] = "ERROR,E"
            devidx += 1

    # Schema appended for fixed ctrs
    nr_fixed = len(self.fix_registers)
    for i in range(0, nr_fixed):
      dev_schema.append(event_map['FIXED' + str(i)])

    dev_schema_desc = ' '.join(dev_schema) + '\n'

    del self.job.schemas[self.name]
    self.job.get_schema(self.name, dev_schema_desc)

  ## Remap data to human readable schema
  def register(self, host):
    """Remap host stats for this device to new schema (drop CTL columns, keep CTR and FIXED).

        """
    # Build stats without ctl registers
    if self.name not in host.stats:
      return
    stats = host.stats[self.name]
    dev_stats = dict(
        (str(i),
         numpy.zeros((len(self.job.times), len(self.ctr_registers) +
                      len(self.fix_registers)), numpy.uint64))
        for i in stats.keys())

    for dev, array in stats.items():
      data = dev_stats[dev]
      idx = 0
      for j in self.ctr_registers:
        data[:, idx] = numpy.array(array[:, j], numpy.uint64)
        idx += 1
      for j in self.fix_registers:
        data[:, idx] = numpy.array(array[:, j], numpy.uint64)
        idx += 1

    host.stats[self.name] = dev_stats


intel_xeon = {
    'intel_snb': cpu_event_map,
    'intel_snb_cbo': cbo_event_map,
    'intel_snb_hau': hau_event_map,
    'intel_snb_imc': imc_event_map,
    'intel_snb_qpi': qpi_event_map,
    'intel_snb_pcu': pcu_event_map,
    'intel_snb_r2pci': r2pci_event_map,
    'intel_ivb': cpu_event_map,
    'intel_ivb_cbo': cbo_event_map,
    'intel_ivb_hau': hau_event_map,
    'intel_ivb_imc': imc_event_map,
    'intel_ivb_qpi': qpi_event_map,
    'intel_ivb_pcu': pcu_event_map,
    'intel_ivb_r2pci': r2pci_event_map,
    'intel_hsw': cpu_event_map,
    'intel_hsw_cbo': cbo_event_map,
    'intel_hsw_hau': hau_event_map,
    'intel_hsw_imc': imc_event_map,
    'intel_hsw_qpi': qpi_event_map,
    'intel_hsw_pcu': pcu_event_map,
    'intel_hsw_r2pci': r2pci_event_map,
    'intel_hsw_ht': cpu_event_map,
    'intel_hsw_cbo_ht': cbo_event_map,
    'intel_knl': cpu_event_map,
    'intel_skx': cpu_event_map,
    'intel_8pmc3': cpu_event_map,
    'intel_4pmc3': cpu_event_map,
    'intel_skx_imc': imc_event_map,
    'intel_skx_cha': cha_event_map
}


def format_knl(job, typename):
  """Split KNL edc/mc stats by func and apply appropriate event map (uclk/eclk, etc.); replace schema and host stats.

    """
  if typename in job.schemas:
    schema_desc = job.schemas.get(typename).desc
    registers = schema_desc.split()

    for host in job.hosts.values():
      if typename not in host.stats:
        return
      stats = host.stats[typename]
      for device, values in stats.items():
        func = device.split('.')[-1]
        if typename == 'intel_knl_edc' and func == '0':
          event_map = knl_edc_uclk_event_map
          name = "intel_knl_edc_uclk"
        elif typename == 'intel_knl_edc' and func == '2':
          event_map = knl_edc_eclk_event_map
          name = "intel_knl_edc_eclk"
        elif typename == 'intel_knl_mc' and func == '0':
          event_map = knl_mc_uclk_event_map
          name = "intel_knl_mc_uclk"
        elif typename == 'intel_knl_mc' and func in ['2', '3', '4']:
          event_map = knl_mc_dclk_event_map
          name = "intel_knl_mc_dclk"
        else:
          log_print(typename + " function " + func + " unknown")
          continue

        host.stats.setdefault(name, {device: 0})
        schema = []
        ctr_idx = []
        for idx, r in enumerate(registers):
          ctl, c = r.split(',')[0:2]
          if c == 'C':
            try:
              if min(values[:, idx]) == max(values[:, idx]):
                schema += [event_map[values[0, idx]]]
              else:
                schema += ["ERROR,E"]
              ctr_idx += [
                  registers.index("CTR" + ctl.lstrip("CTL") + ",E,W=48")
              ]
            except:
              continue
        values = values[:, ctr_idx]
        host.stats[name][device] = values
        job.get_schema(name, ' '.join(schema) + '\n')
      del host.stats[typename]
    del job.schemas[typename]


def process_job(job):
  """Apply reformat_counters for all intel_xeon device types in job.schemas; run format_knl for KNL edc/mc; handle legacy intel_pmc3/wtm/uncore/nhm.

    """
  # These events work for SNB,IVB,HSW,BDW,SKX at this time 2015/05/27
  for device, mapping in intel_xeon.items():
    if device in job.schemas:
      d = reformat_counters(job, device, mapping)
      for host in job.hosts.values():
        d.register(host)

  format_knl(job, 'intel_knl_edc')
  format_knl(job, 'intel_knl_mc')

  # Backwards compatibility
  if 'intel_pmc3' in job.schemas:
    wtm = reformat_counters(job, 'intel_pmc3', wtm_event_map)
    for host in job.hosts.values():
      wtm.register(host)

  if 'intel_wtm' in job.schemas:
    wtm = reformat_counters(job, 'intel_wtm', wtm_event_map)
    for host in job.hosts.values():
      wtm.register(host)

  if 'intel_uncore' in job.schemas:
    wtmunc = reformat_counters(job, 'intel_uncore', wtmunc_event_map)
    for host in job.hosts.values():
      wtmunc.register(host)

  # NHM has the same events as WTM (for our purposes)
  if 'intel_nhm' in job.schemas:
    nhm = reformat_counters(job, 'intel_nhm', wtm_event_map)
    for host in job.hosts.values():
      nhm.register(host)
