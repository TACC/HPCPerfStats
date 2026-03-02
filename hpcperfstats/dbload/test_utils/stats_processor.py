import os
import sys
import time
from datetime import datetime, timedelta
from multiprocessing import Pool

from pandas import DataFrame, concat, to_datetime

#import pandas
#pandas.set_option('display.max_rows', 100)

amd64_pmc_eventmap = { 0x43ff03 : "FLOPS,W=48", 0x4300c2 : "BRANCH_INST_RETIRED,W=48", 0x4300c3: "BRANCH_INST_RETIRED_MISS,W=48",
                       0x4308af : "DISPATCH_STALL_CYCLES1,W=48", 0x43ffae :"DISPATCH_STALL_CYCLES0,W=48" }

amd64_df_eventmap = { 0x403807 : "MBW_CHANNEL_0,W=48,U=64B", 0x403847 : "MBW_CHANNEL_1,W=48,U=64B", 0x403887 : "MBW_CHANNEL_2,W=48,U=64B" ,
                      0x4038c7 : "MBW_CHANNEL_3,W=48,U=64B", 0x433907 : "MBW_CHANNEL_4,W=48,U=64B", 0x433947 : "MBW_CHANNEL_5,W=48,U=64B",
                      0x433987 : "MBW_CHANNEL_6,W=48,U=64B", 0x4339c7 : "MBW_CHANNEL_7,W=48,U=64B" }

intel_8pmc3_eventmap = { 0x4301c7 : 'FP_ARITH_INST_RETIRED_SCALAR_DOUBLE,W=48,U=1',      0x4302c7 : 'FP_ARITH_INST_RETIRED_SCALAR_SINGLE,W=48,U=1',
                         0x4304c7 : 'FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE,W=48,U=2', 0x4308c7 : 'FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE,W=48,U=4',
                         0x4310c7 : 'FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE,W=48,U=4', 0x4320c7 : 'FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE,W=48,U=8',
                         0x4340c7 : 'FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE,W=48,U=8', 0x4380c7 : 'FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE,W=48,U=16',
                         "FIXED_CTR0" : 'INST_RETIRED,W=48', "FIXED_CTR1" : 'APERF,W=48', "FIXED_CTR2" : 'MPERF,W=48' }

intel_skx_imc_eventmap = {0x400304 : "CAS_READS,W=48", 0x400c04 : "CAS_WRITES,W=48", 0x400b01 : "ACT_COUNT,W=48", 0x400102 : "PRE_COUNT_MISS,W=48"}

"""
{ time : 1628783584.433181, host : amd-1, jobid : 101, type : amd64_rapl, device : 0, event : MSR_CORE_ENERGY_STAT, unit : mJ, width : 32, value : 1668055930 } 

"""


exclude_typs = ["block", "ib", "ib_sw", "intel_skx_cha", "mdc", "numa", "osc", "proc", "ps", "sysv_shm", "tmpfs", "vfs", "vm"]

def process(stats_file):

    with open(stats_file, 'r') as fd:
        lines = fd.readlines()

    schema = {}
    stats  = []

    start = time.time()
    for line in lines:
        if not line[0]: continue

        if line[0].isalpha():
            typ, dev, vals = line.split(maxsplit = 2)
            vals = vals.split()
            if typ in exclude_typs: continue

            # Mapping hardware counters to events
            if typ == "amd64_pmc" or typ == "amd64_df" or typ == "intel_8pmc3" or typ == "intel_skx_imc":
                if typ == "amd64_pmc": eventmap = amd64_pmc_eventmap
                if typ == "amd64_df": eventmap = amd64_df_eventmap
                if typ == "intel_8pmc3": eventmap = intel_8pmc3_eventmap
                if typ == "intel_skx_imc": eventmap = intel_skx_imc_eventmap
                n = {}
                rm_idx = []
                schema_mod = []*len(schema[typ])

                for idx, eve in enumerate(schema[typ]):

                    eve = eve.split(',')[0]
                    if "CTL" in eve:
                        try:
                            n[eve.lstrip("CTL")] = eventmap[int(vals[idx])]
                        except:
                            n[eve.lstrip("CTL")] = "OTHER"
                        rm_idx += [idx]

                    elif "FIXED_CTR" in eve:
                        schema_mod += [eventmap[eve]]

                    elif "CTR" in eve:
                        schema_mod += [n[eve.lstrip("CTR")]]
                    else:
                        schema_mod += [eve]

                for idx in sorted(rm_idx, reverse = True): del vals[idx]
                vals = dict(zip(schema_mod, vals))
            else:
                # Software counters are not programmable and do not require mapping
                vals = dict(zip(schema[typ], vals))

            rec  =  { **tags, "typ" : typ, "dev" : dev }

            for eve, val in vals.items():
                eve = eve.split(',')
                width = 64
                mult = 1
                unit = "#"

                for ele in eve[1:]:
                    if "W=" in ele: width = int(ele.lstrip("W="))
                    if "U=" in ele:
                        ele = ele.lstrip("U=")
                        try:    mult = float(''.join(filter(str.isdigit, ele)))
                        except: pass
                        try:    unit = ''.join(filter(str.isalpha, ele))
                        except: pass

                stats += [ { **rec, "eve" : eve[0], "val" : float(val), "wid" : width, "mult" : mult, "unit" : unit } ]

        elif line[0].isdigit():
            t, jid, host = line.split()
            tags = { "time" : float(t), "host" : host, "jid" : jid }
        elif line[0] == '!':
            label, events = line.split(maxsplit = 1)
            typ, events = label[1:], events.split()
            schema[typ] = events

    stats = DataFrame.from_records(stats)

    # compute difference between time adjacent stats
    stats["dif"] = (stats.groupby(["host", "jid", "typ", "dev", "eve"])["val"].diff()).fillna(0)

    # correct stats for rollover and units
    stats["dif"].mask(stats["dif"] < 0, 2**stats["wid"] + stats["dif"], inplace = True)
    stats["dif"] = stats["dif"] * stats["mult"]
    del stats["wid"], stats["mult"]

    # aggregate over devices
    stats = stats.groupby(["time", "host", "jid", "typ", "eve", "unit"]).sum().reset_index()

    # compute average rate of change
    deltat = stats.groupby(["host", "jid", "typ", "eve"])["time"].diff().fillna(0)
    stats["arc"] = (stats["dif"]/deltat).fillna(0)

    stats["time"] = to_datetime(stats["time"], unit = 's')
    print("processing time for {0} {1:.1f}s".format(stats_file, time.time() - start))
    return stats

# Aggregate over devices and events in event_list
def agg(df, typename, event_list, tags):
    datam = df[(df["typ"].values == typename) & (df["eve"].isin(event_list))].groupby(tags).sum()
    return datam

#################################################################
startdate = datetime.strptime(sys.argv[1], "%Y-%m-%d")
try:
    enddate   = datetime.strptime(sys.argv[2], "%Y-%m-%d")
except:
    enddate = startdate + timedelta(days = 1)
print("Start Date: ", startdate)
print("Start End:  ",  enddate)
#################################################################

# Parse and convert raw stats files to pandas dataframe
start = time.time()
directory = "/fstats/archive"
stats_df = DataFrame()

stats_files = []
for entry in os.scandir(directory):
    if entry.is_file() or not entry.name.startswith("c"): continue
    for stats_file in os.scandir(entry.path):
        if not stats_file.is_file() or stats_file.name.startswith('.'): continue
        if stats_file.name.startswith("current"): continue
        try:
            fdate = datetime.fromtimestamp(int(stats_file.name))
        except: continue
        if  fdate < startdate - timedelta(days = 1) or fdate > enddate: continue
        stats_files += [stats_file.path]

print("Number of host stats files to process = ", len(stats_files))

with Pool(processes = 48) as pool:
    stats_df = concat(pool.imap(process, stats_files), ignore_index = True)

print("loading time", time.time() - start)
print(stats_df)
stats_df.to_pickle("all.pkl")

### Basic stats data ingested
tags = ["host", "jid", "time"]

data = DataFrame()
data = stats_df.groupby(tags).sum()
del data["val"]

### Flops
## ZEN2/3
event_list = ["FLOPS"]
datam = agg(stats_df, "amd64_pmc", event_list, tags)
if not datam.empty:
    data["flops,GF"] = 1e-9*datam["arc"]

## SKX/CLX
event_list = ['FP_ARITH_INST_RETIRED_SCALAR_DOUBLE', 'FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE',
              'FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE', 'FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE']
datam = agg(stats_df, "intel_8pmc3", event_list, tags)

if not datam.empty:
    data["flops64b,GF"] = 1e-9*datam["arc"]

event_list = ['FP_ARITH_INST_RETIRED_SCALAR_SINGLE', 'FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE',
              'FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE', 'FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE']

datam = agg(stats_df, "intel_8pmc3", event_list, tags)
if not datam.empty:
    data["flops32b,GF"] = 1e-9*datam["arc"]

# CPI & Frequency

event_list = ["INST_RETIRED"]
datam = agg(stats_df, "amd64_pmc", event_list, tags)
if not datam.empty:
    data["inst_retired"] = datam["dif"]

datam = agg(stats_df, "intel_8pmc3", event_list, tags)
if not datam.empty:
    data["inst_retired"] = datam["dif"]

event_list = ["APERF"]
datam = agg(stats_df, "amd64_pmc", event_list, tags)
if not datam.empty:
    data["cycles"] = datam["dif"]

datam = agg(stats_df, "intel_8pmc3", event_list, tags)
if not datam.empty:
    data["cycles"] = datam["dif"]

event_list = ["MPERF"]
datam = agg(stats_df, "amd64_pmc", event_list, tags)
if not datam.empty:
    data["ref_cycles"] = datam["dif"]
    freq = 2.45

datam = agg(stats_df, "intel_8pmc3", event_list, tags)
if not datam.empty:
    data["ref_cycles"] = datam["dif"]
    freq = 2.7

data["cpi"] = (data["cycles"] / data["inst_retired"]).fillna(0)
data["freq"] = freq*(data["cycles"] / data["ref_cycles"]).fillna(0)

# Membw
event_list = ["MBW_CHANNEL_0", "MBW_CHANNEL_1", "MBW_CHANNEL_2", "MBW_CHANNEL_3"]
datam = agg(stats_df, "amd64_df", event_list, tags)

if not datam.empty:
    data["mbw,GB/s"] = datam["arc"]*2/(1024*1024*1024)

event_list = ["CAS_READS", "CAS_WRITES"]
datam = agg(stats_df, "intel_skx_imc", event_list, tags)
if not datam.empty:
    data["mbw,GB/s"] = datam["arc"]*64/(1024*1024*1024)

# CPU Usage
event_list = ["user", "nice", "system"]
datam = agg(stats_df, "cpu", event_list, tags)
data["cpu,cores"] = 0.01*datam["arc"]

# Mem Usage
event_list =[ "MemUsed" ]
datam = agg(stats_df, "mem", event_list, tags)
data["mem,GB"] = (1.0/(1024*1024))*datam["val"]

# IB Usage
event_list = ["port_xmit_data", "port_rcv_data"]
datam = agg(stats_df, "ib_ext", event_list, tags)
data["ibbw,MB/s"] = datam["arc"]/(1024*1024)

# Lustre MDS Usage
event_list = ["open", "close", "mmap", "seek", "fsync", "setattr", "truncate",
              "flock", "getattr", "statfs", "alloc_inode", "setxattr", "getxattr",
              "listxattr", "removexattr", "inode_permission", "readdir", "create",
              "lookup", "link", "unlink", "symlink", "mkdir", "rmdir", "mknod", "rename"]
datam = agg(stats_df, "llite", event_list, tags)
data["liops,#/s"] = datam["arc"]

# Lustre BW Usage
event_list = ["read_bytes", "write_bytes"]
datam = agg(stats_df, "llite", event_list, tags)
data["lbw,MB/s"] = datam["arc"]/(1024*1024)

# Ethernet BW Usage
event_list = ["rx_bytes", "tx_bytes"]
datam = agg(stats_df, "net", event_list, tags)
data["ethbw,MB/s"] = datam["arc"]/(1024*1024)

del data["dif"], data["arc"]
print(data)
data = data.reset_index()
#print(data[data.jid == "3477979"])
sys.exit()



