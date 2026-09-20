# Workload Version Log

Both baseline and PNM runs share the same benchmark parameters for a fair comparison. Two
committed result sets use the current workload; earlier development runs (v1 and v2, 15,000 keys)
are not kept in the repository. New runs of the configs write to `m5out/baseline/` and
`m5out/pnm/`.

---

## v3  (`m5out/baseline_v3/`, `m5out/pnm_v3/`)

| Parameter | Value |
|-----------|-------|
| Benchmarks | `fillrandom`, `readrandom`, `waitforcompaction` |
| Keys (`--num`) | 25,000 |
| Seed | 42 |
| Value size | 1,024 B |
| Block size | 4,096 B |
| Write buffer size | 1 MiB |
| Block cache | 8 MiB |

**Notes:** Uses 25,000 keys (up from 15,000 in the earlier development runs) to stress the
compaction pipeline further. At 25,000 keys, five L0→L1 compaction jobs fire, and the larger
working set exceeds the 8 MiB block cache, making read-path bandwidth the bottleneck for both
configurations. These are the results reported in the README and the paper.

**Key results:** Write +58.6% (29,141 → 46,217 ops/sec), read +25.0% (18,264 → 22,834 ops/sec),
DRAM reads −12%, L2 misses −64%, end-to-end sim time −0.9%.
See [comp_v3.md](comp_v3.md).

---

## clean  (`m5out/baseline_clean/`, `m5out/pnm_clean/`)

| Parameter | Value |
|-----------|-------|
| Benchmarks | `fillrandom`, `readrandom`, `waitforcompaction` |
| Keys (`--num`) | 25,000 |
| Seed | 42 |
| Value size | 1,024 B |
| Block size | 4,096 B |
| Write buffer size | 1 MiB |
| Block cache | 8 MiB |

**Notes:** Same workload parameters as v3. Re-run after a full comment/naming/structural
cleanup of `configs/yonsei/` and all documentation — no benchmark parameter changes.

**Key results:** Write +44.7% (29,381 → 42,523 ops/sec), read +12.9% (21,913 → 24,731 ops/sec).
End-to-end sim time baseline 7.162 s vs PNM 7.266 s (+1.5% — 2-CPU overhead not yet fully overcome
at this run, but application throughput is strongly PNM-favorable). See [comp_v3.md](comp_v3.md) for
the v3 run's cache/DRAM/compaction breakdown (same workload parameters; application throughput
differs between runs due to KVM boot non-determinism).
