# Workloads

Every committed run, baseline and PNM alike, uses the same `db_bench` workload:

| Parameter | Value |
|-----------|-------|
| Benchmarks | `fillrandom`, `readrandom`, `waitforcompaction` |
| Keys (`--num`) | 25,000 |
| Seed | 42 |
| Value size | 1,024 B |
| Block size | 4,096 B |
| Write buffer size | 1 MiB |
| Block cache | 8 MiB |

At 25,000 keys five L0→L1 compaction jobs fire, and the data exceeds the 8 MiB block cache, so the
read phase is bandwidth-bound in both configurations.

| Result set | Directories under `m5out/` | Code | Run |
|------------|----------------------------|------|-----|
| v3 | `baseline_v3/`, `pnm_v3/` | tag `capstone` | KVM boot, switch to TIMING at the region of interest; the paper's figures |
| clean | `baseline_clean/`, `pnm_clean/` | tag `capstone` | A second KVM run; `config.ini` byte-identical to v3 |
| post | `baseline_post/`, `baseline_2core_post/`, `pnm_post/` | `main` | Atomic-CPU boot without systemd, checkpoint at `m5 workbegin`, restore on TIMING CPUs; adds the two-core baseline |

Results are in the README: [Results](../../README.md#results) for v3 and clean,
[Results with the current code](../../README.md#results-with-the-current-code) for post. A KVM boot
is not deterministic, so v3 and clean differ; each post run repeats exactly from its checkpoint.
The PNM runs' simulated time includes a 0.5 s startup pause, in which `rocksdb_pnm` waits for its
worker inside the measured region.

New runs of the configs write to `m5out/baseline/` and `m5out/pnm/` (`m5out/baseline_2core/` with
two cores; [GEM5_CUSTOMIZATIONS.md](GEM5_CUSTOMIZATIONS.md) lists the suffixes of atomic and
checkpoint runs).
