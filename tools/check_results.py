#!/usr/bin/env python3
"""
Recompute every number the README and the paper report from the committed simulation outputs.

    python tools/check_results.py

Reads the two run pairs under gem5/m5out/ (board.pc.com_1.device for the RocksDB db_bench output,
stats.txt for the gem5 counters of the region of interest) and compares each derived value with
the figure quoted in README.md, rounded the same way:

  baseline_v3 / pnm_v3        the headline run, which the paper also reports
  baseline_clean / pnm_clean  the repeat run of the same configuration

Needs only Python 3, no packages; it does not run gem5.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
M5OUT = REPO / "gem5" / "m5out"


def device(run: str) -> dict[str, float]:
    text = (M5OUT / run / "board.pc.com_1.device").read_text(errors="ignore")
    out: dict[str, float] = {}
    for phase in ("fillrandom", "readrandom"):
        m = re.search(rf"^{phase}\s*:\s*([\d.]+) micros/op (\d+) ops/sec ([\d.]+) seconds "
                      rf"\d+ operations;\s*([\d.]+) MB/s", text, re.M)
        if not m:
            sys.exit(f"{run}: no {phase} summary line")
        out[f"{phase}.latency"] = float(m.group(1))
        out[f"{phase}.ops"] = float(m.group(2))
        out[f"{phase}.seconds"] = float(m.group(3))
        out[f"{phase}.mbps"] = float(m.group(4))
    m = re.search(r"rocksdb\.compaction\.times\.micros .*COUNT : (\d+) SUM : (\d+)", text)
    out["compaction.count"], out["compaction.micros"] = float(m.group(1)), float(m.group(2))
    m = re.search(r"rocksdb\.compaction\.key\.drop\.new COUNT : (\d+)", text)
    out["key.drop.new"] = float(m.group(1))
    return out


def stats(run: str) -> dict[str, float]:
    # the first statistics dump covers the region of interest; later dumps are the shutdown
    text = (M5OUT / run / "stats.txt").read_text(errors="ignore")
    roi = text.split("---------- End Simulation Statistics")[0]

    def value(pattern: str) -> float:
        vals = [float(v) for v in re.findall(rf"^{pattern}\s+([\d.]+)", roi, re.M)]
        if not vals:
            sys.exit(f"{run}: stat {pattern!r} not found")
        return sum(vals)

    return {
        "simSeconds": value(r"simSeconds"),
        "l2.misses": value(r"board\.cache_hierarchy\.l2-cache-0\.demandMisses::total"),
        "readReqs": value(r"board\.memory\.mem_ctrl\d\.readReqs"),
        "bytesRead": value(r"board\.memory\.mem_ctrl\d\.dram\.bytesRead::total"),
        "bytesWritten": value(r"board\.memory\.mem_ctrl\d\.dram\.bytesWritten::total"),
    }


def pair(baseline: str, pnm: str) -> tuple[dict[str, float], dict[str, float]]:
    return ({**device(baseline), **stats(baseline)}, {**device(pnm), **stats(pnm)})


def main() -> None:
    b, p = pair("baseline_v3", "pnm_v3")

    def change(key: str) -> float:
        return 100.0 * (p[key] - b[key]) / b[key]

    def ratio(num: float, den: float) -> float:
        return num / den

    checks = [
        # README results table
        ("write throughput, ops/s", f"{b['fillrandom.ops']:,.0f} -> {p['fillrandom.ops']:,.0f}",
         "29,141 -> 46,217"),
        ("write throughput change", f"{change('fillrandom.ops'):+.1f}%", "+58.6%"),
        ("write latency, us/op", f"{b['fillrandom.latency']:.1f} -> {p['fillrandom.latency']:.1f}",
         "34.3 -> 21.6"),
        ("write latency change", f"{change('fillrandom.latency'):+.1f}%", "-36.9%"),
        ("read throughput, ops/s", f"{b['readrandom.ops']:,.0f} -> {p['readrandom.ops']:,.0f}",
         "18,264 -> 22,834"),
        ("read throughput change", f"{change('readrandom.ops'):+.1f}%", "+25.0%"),
        ("read latency, us/op", f"{b['readrandom.latency']:.1f} -> {p['readrandom.latency']:.1f}",
         "54.8 -> 43.8"),
        ("read latency change", f"{change('readrandom.latency'):+.1f}%", "-20.0%"),
        ("compaction wall-clock, us",
         f"{b['compaction.micros']:,.0f} -> {p['compaction.micros']:,.0f}",
         "1,595,756 -> 1,118,829"),
        ("compaction wall-clock change", f"{change('compaction.micros'):+.1f}%", "-29.9%"),
        ("compaction jobs", f"{b['compaction.count']:.0f} / {p['compaction.count']:.0f}", "5 / 5"),
        ("superseded keys dropped (baseline)", f"{b['key.drop.new']:,.0f}", "8,441"),
        ("DRAM read requests, M", f"{b['readReqs'] / 1e6:.2f} -> {p['readReqs'] / 1e6:.2f}",
         "16.07 -> 14.15"),
        ("DRAM read requests change", f"{change('readReqs'):+.1f}%", "-12.0%"),
        ("CPU0 L2 misses, M", f"{b['l2.misses'] / 1e6:.2f} -> {p['l2.misses'] / 1e6:.2f}",
         "11.06 -> 3.94"),
        ("CPU0 L2 misses change", f"{change('l2.misses'):+.1f}%", "-64.3%"),
        # paper, Figure 5 (full-system mode), normalized PNM / baseline
        ("paper: L2 cache misses", f"{ratio(p['l2.misses'], b['l2.misses']):.3f}x", "0.357x"),
        ("paper: compaction time",
         f"{ratio(p['compaction.micros'], b['compaction.micros']):.3f}x", "0.701x"),
        # the paper sums the per-op latencies as rounded in the results table (0.1 us)
        ("paper: latency (write + read)",
         f"{ratio(round(p['fillrandom.latency'], 1) + round(p['readrandom.latency'], 1), round(b['fillrandom.latency'], 1) + round(b['readrandom.latency'], 1)):.3f}x",
         "0.734x"),
        ("paper: bandwidth (write + read MB/s)",
         f"{ratio(p['fillrandom.mbps'] + p['readrandom.mbps'], b['fillrandom.mbps'] + b['readrandom.mbps']):.2f}x",
         "1.49x"),
        ("paper: memory traffic (bytes read + written)",
         f"{ratio(p['bytesRead'] + p['bytesWritten'], b['bytesRead'] + b['bytesWritten']):.3f}x",
         "0.895x"),
        ("paper: execution time (write + read phases)",
         f"{ratio(p['fillrandom.seconds'] + p['readrandom.seconds'], b['fillrandom.seconds'] + b['readrandom.seconds']):.3f}x",
         "0.735x"),
    ]

    # the repeat run of the same configuration, README "Repeat run"
    b2, p2 = pair("baseline_clean", "pnm_clean")

    def change2(key: str) -> float:
        return 100.0 * (p2[key] - b2[key]) / b2[key]

    checks += [
        ("repeat: write throughput, ops/s",
         f"{b2['fillrandom.ops']:,.0f} -> {p2['fillrandom.ops']:,.0f}", "29,381 -> 42,523"),
        ("repeat: write throughput change", f"{change2('fillrandom.ops'):+.1f}%", "+44.7%"),
        ("repeat: read throughput, ops/s",
         f"{b2['readrandom.ops']:,.0f} -> {p2['readrandom.ops']:,.0f}", "21,913 -> 24,731"),
        ("repeat: read throughput change", f"{change2('readrandom.ops'):+.1f}%", "+12.9%"),
        ("repeat: compaction wall-clock change", f"{change2('compaction.micros'):+.1f}%", "-14.4%"),
        ("repeat: CPU0 L2 misses, M",
         f"{b2['l2.misses'] / 1e6:.2f} -> {p2['l2.misses'] / 1e6:.2f}", "9.87 -> 4.56"),
        ("repeat: CPU0 L2 misses change", f"{change2('l2.misses'):+.1f}%", "-53.8%"),
        ("repeat: baseline compaction jobs", f"{b2['compaction.count']:.0f}", "4"),
    ]

    failed = 0
    for name, got, expected in checks:
        ok = got.replace(",", "") == expected.replace(",", "")
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:46s} {got:>26s}   (quoted: {expected})")

    print(f"\n{len(checks) - failed}/{len(checks)} quoted figures reproduced from the committed logs")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
