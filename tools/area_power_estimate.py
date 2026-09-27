#!/usr/bin/env python3
"""
Order-of-magnitude area and power of the PNM compaction unit against the general-purpose core that
the two-core control gives RocksDB instead. An estimate from published process constants and stated
assumptions, not a simulation result; every input is a (low, high) range and both ends are printed.

    python tools/area_power_estimate.py

Both sides are placed in the same process, TSMC N7, so the node cancels out of the ratio: a DIMM
buffer chip is built in an older node, which would scale both sides alike.

Published constants
  SRAM bitcell, N7 high-density   0.027 um^2        TSMC, IEDM 2016 ("A 7nm CMOS platform technology
                                                    ... with a 0.027um2 high density 6-T SRAM cell")
  Logic density, N7 high-density  91.2 MTr/mm^2     WikiChip Fuse, "TSMC 7nm HD and HP cells"
  Cortex-A76 at 7 nm              750 mW per core   Arm's launch figure for the core's power point

Assumptions (ranges)
  gate equivalent (GE) = one NAND2 = 4 transistors; a flip-flop bit = 5-7 GE
  placement utilization of the logic 50-80 %; SRAM array efficiency 50-70 %
  switched capacitance per GE incl. wiring 0.5-1.5 fF; activity 0.1-0.3; supply 0.75 V
  unit clock 0.6-1.2 GHz (a DDR4-2400 buffer chip's command clock is 1.2 GHz)
"""

from __future__ import annotations

SRAM_UM2_PER_BIT = 0.027
TR_PER_MM2 = 91.2e6
TR_PER_GE = 4
FF_GE = (5, 7)
UTIL = (0.8, 0.5)             # (low-area end, high-area end)
SRAM_EFF = (0.7, 0.5)
C_GE_FF = (0.5e-15, 1.5e-15)  # farads
ACTIVITY = (0.1, 0.3)
VDD = 0.75
F_UNIT = (0.6e9, 1.2e9)
CORE_W = 0.75                 # Cortex-A76, one core at 7 nm

# The unit as the paper specifies it (Sections 3.2-3.5): fixed 1,040-byte records (16-byte key),
# two 128-bit key buffers with valid bits, a last-key register for deduplication, an address
# generation unit (index x 1040 + base), a 2-way 128-bit comparator, an MMIO register file and a
# DMA/retry controller that latches one pending 64-byte packet.
PAPER_FF_BITS = (
    2 * 128 + 2          # key buffers and valid bits
    + 128                # last written key
    + 3 * 64 + 32 + 64   # register file: two input bases, output base, count, command, status
    + 2 * 32 + 3 * 64    # AGU: two record indices, three current addresses
    + 512 + 64 + 8       # pending packet: data, address, control
    + 32                 # FSM state and beat counters
)
PAPER_LOGIC_GE = (
    128 * 6              # 128-bit magnitude comparator (minimum of the two keys)
    + 128 * 2.5          # 128-bit equality against the last key (deduplication)
    + 128 * 2.5          # 2:1 key select
    + 512 * 2.5          # 2:1 select of the 64-byte data beat to write
    + 4 * 64 * 10        # four 64-bit adders in the AGU
)
PAPER_CONTROL_GE = (3_000, 10_000)   # FSM, DMA/retry control, MMIO decode, bus interface

# What a unit that compacts RocksDB's real SST format would add: a 4 KiB block buffer per input and
# one for the output, each double-buffered; CRC32C; block decode and encode (varints, restart points,
# key prefixes).
SST_SRAM_BITS = 3 * 2 * 4096 * 8
SST_LOGIC_GE = (25_000, 65_000)

# The control's general-purpose core, as simulated: 32 KiB L1-I + 32 KiB L1-D + 512 KiB L2. Only the
# caches' SRAM is counted, so this is a lower bound on the core's area: its pipeline is left out.
CORE_CACHE_BITS = (32 + 32 + 512) * 1024 * 8


def logic_mm2(ge: float, util: float) -> float:
    return ge * TR_PER_GE / (TR_PER_MM2 * util)


def sram_mm2(bits: float, eff: float) -> float:
    return bits * SRAM_UM2_PER_BIT / eff / 1e6


def unit(extra_bits: int, extra_ge: tuple[float, float]) -> dict[str, tuple[float, float]]:
    ff_ge = [PAPER_FF_BITS * g for g in FF_GE]
    comb = [PAPER_LOGIC_GE + c + e for c, e in zip(PAPER_CONTROL_GE, extra_ge)]
    ge = [f + c for f, c in zip(ff_ge, comb)]
    area = [logic_mm2(g, u) + sram_mm2(extra_bits, s) for g, u, s in zip(ge, UTIL, SRAM_EFF)]
    # dynamic power: combinational switching at the activity factor, plus flip-flop clocking
    power = [
        a * g * c * VDD ** 2 * f + PAPER_FF_BITS * 2 * c * VDD ** 2 * f
        for a, g, c, f in zip(ACTIVITY, ge, C_GE_FF, F_UNIT)
    ]
    return {"ge": tuple(ge), "mm2": tuple(area), "w": tuple(power)}


def main() -> None:
    paper = unit(0, (0, 0))
    sst = unit(SST_SRAM_BITS, SST_LOGIC_GE)
    caches = tuple(sram_mm2(CORE_CACHE_BITS, e) for e in SRAM_EFF)

    print(f"{'':44}{'area (mm^2, N7)':>22}{'dynamic power':>22}")
    for name, u in (("PNM unit as specified in the paper", paper),
                    ("PNM unit for RocksDB's SST format", sst)):
        print(f"{name:44}{u['mm2'][0]:>10.4f} - {u['mm2'][1]:<9.4f}"
              f"{u['w'][0] * 1e3:>10.1f} - {u['w'][1] * 1e3:.1f} mW"
              f"   ({u['ge'][0] / 1e3:.0f}k-{u['ge'][1] / 1e3:.0f}k GE)")
    print(f"{'Control core: its 576 KiB of caches alone':44}{caches[0]:>10.4f} - {caches[1]:<9.4f}"
          f"{'(Cortex-A76: 750 mW)':>22}")
    print()
    for name, u in (("paper unit", paper), ("SST-format unit", sst)):
        area_ratio = (caches[0] / u["mm2"][1], caches[1] / u["mm2"][0])
        power_ratio = (CORE_W / u["w"][1], CORE_W / u["w"][0])
        print(f"The core's caches alone are {area_ratio[0]:.0f}-{area_ratio[1]:.0f}x the {name}'s area; "
              f"a Cortex-A76 draws {power_ratio[0]:.0f}-{power_ratio[1]:.0f}x its power.")


if __name__ == "__main__":
    main()
