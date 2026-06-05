"""Legacy stats-file decode: CTL/CTR/FIXED_CTR and hex eventmaps for historical archives."""
from __future__ import annotations

from hpcperfstats.monitor_naming.legacy import (
    INGEST_LEGACY_KNL_IMC_TYPE,
    MONITOR_LEGACY_KNL_IMC_TYPE,
)

amd64_pmc_eventmap = {
    0x43ff03: "FLOPS,W=48",
    0x4300c2: "BRANCH_INST_RETIRED,W=48",
    0x4300c3: "BRANCH_INST_RETIRED_MISS,W=48",
    0x4308af: "DISPATCH_STALL_CYCLES1,W=48",
    0x43ffae: "DISPATCH_STALL_CYCLES0,W=48",
}

amd64_df_eventmap = {
    0x403807: "MBW_CHANNEL_0,W=48,U=64B",
    0x403847: "MBW_CHANNEL_1,W=48,U=64B",
    0x403887: "MBW_CHANNEL_2,W=48,U=64B",
    0x4038c7: "MBW_CHANNEL_3,W=48,U=64B",
    0x433907: "MBW_CHANNEL_4,W=48,U=64B",
    0x433947: "MBW_CHANNEL_5,W=48,U=64B",
    0x433987: "MBW_CHANNEL_6,W=48,U=64B",
    0x4339c7: "MBW_CHANNEL_7,W=48,U=64B",
}

intel_8pmc3_eventmap = {
    0x4301c7: "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE,W=48,U=1",
    0x4302c7: "FP_ARITH_INST_RETIRED_SCALAR_SINGLE,W=48,U=1",
    0x4304c7: "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE,W=48,U=2",
    0x4308c7: "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE,W=48,U=4",
    0x4310c7: "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE,W=48,U=4",
    0x4320c7: "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE,W=48,U=8",
    0x4340c7: "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE,W=48,U=8",
    0x4380c7: "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE,W=48,U=16",
    0x438010: "SSE_DOUBLE_SCALAR,W=48,U=1",
    0x431010: "SSE_DOUBLE_PACKED,W=48,U=2",
    0x430211: "SIMD_DOUBLE_256,W=48,U=4",
    0x439010: "SSE_DOUBLE_ALL,W=48,U=1",
    "FIXED_CTR0": "INST_RETIRED,W=48",
    "FIXED_CTR1": "APERF,W=48",
    "FIXED_CTR2": "MPERF,W=48",
}

intel_skx_imc_eventmap = {
    0x400304: "CAS_READS,W=48",
    0x400c04: "CAS_WRITES,W=48",
    0x400b01: "ACT_COUNT,W=48",
    0x400102: "PRE_COUNT_MISS,W=48",
}

intel_snb_imc_eventmap = {
    0x400304: "CAS_READS,W=48",
    0x400b04: "CAS_WRITES,W=48",
}

intel_ivb_imc_eventmap = intel_snb_imc_eventmap

intel_hsw_imc_eventmap = {
    0x400304: "CAS_READS,W=48",
    0x400b04: "CAS_WRITES,W=48",
}

intel_bdw_imc_eventmap = intel_hsw_imc_eventmap

intel_knl_mc_dclk_eventmap = {
    0x300301: "CAS_READS,W=48",
    0x300309: "CAS_WRITES,W=48",
}

EVENTMAPS_BY_TYPE = {
    "amd64_pmc": amd64_pmc_eventmap,
    "amd64_df": amd64_df_eventmap,
    "intel_8pmc3": intel_8pmc3_eventmap,
    "intel_4pmc3": intel_8pmc3_eventmap,
    "intel_snb_imc": intel_snb_imc_eventmap,
    "intel_ivb_imc": intel_ivb_imc_eventmap,
    "intel_hsw_imc": intel_hsw_imc_eventmap,
    "intel_bdw_imc": intel_bdw_imc_eventmap,
    INGEST_LEGACY_KNL_IMC_TYPE: intel_knl_mc_dclk_eventmap,
    MONITOR_LEGACY_KNL_IMC_TYPE: intel_knl_mc_dclk_eventmap,
    "intel_skx_imc": intel_skx_imc_eventmap,
}

# Historical ingest normalized KNL monitor type to dclk bucket in host_data.
_LEGACY_KNL_TYPE_NORMALIZE = {
    MONITOR_LEGACY_KNL_IMC_TYPE: INGEST_LEGACY_KNL_IMC_TYPE,
}


def map_hardware_counter_vals(typ, schema_events, vals, eventmap):
    """Map CTL/CTR/FIXED_CTR schema rows to legacy event names via hex eventmap."""
    n = {}
    rm_idx = []
    schema_mod = []
    for idx, eve in enumerate(schema_events):
        eve = eve.split(",")[0]
        if "CTL" in eve:
            try:
                n[eve.lstrip("CTL")] = eventmap[int(vals[idx])]
            except Exception:
                n[eve.lstrip("CTL")] = "OTHER"
            rm_idx.append(idx)
        elif "FIXED_CTR" in eve:
            schema_mod.append(eventmap[eve])
        elif "CTR" in eve:
            schema_mod.append(n[eve.lstrip("CTR")])
        else:
            schema_mod.append(eve)
    for idx in sorted(rm_idx, reverse=True):
        del vals[idx]
    return dict(zip(schema_mod, vals))


def legacy_output_type(typ: str) -> str:
    """Normalize legacy typename for host_data (KNL dclk bucket)."""
    return _LEGACY_KNL_TYPE_NORMALIZE.get(typ, typ)


def decode_counter_line(typ: str, schema: dict, vals: list) -> dict | None:
    """Return event->value dict for a legacy hardware-counter line, or None to skip."""
    if typ not in EVENTMAPS_BY_TYPE or typ not in schema:
        return None
    eventmap = EVENTMAPS_BY_TYPE[typ]
    return map_hardware_counter_vals(typ, schema[typ], vals, eventmap)
