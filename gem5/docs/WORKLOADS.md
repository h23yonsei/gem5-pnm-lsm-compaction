# Workloads

Every committed run, baseline and PNM alike, uses the same `db_bench` workload, set by the flags in
`run_baseline.py` and `run_pnm.py`; the key size and the values' compressibility are `db_bench`'s
defaults:

| Parameter | Value |
|-----------|-------|
| Benchmarks | `fillrandom`, `readrandom`, `waitforcompaction` |
| Keys (`--num`) | 25,000, 16 B each |
| Seed | 42 |
| Value size | 1,024 B, generated to compress to half that size |
| Compression (`--compression_type`) | none |
| Compaction style | level |
| L0 compaction trigger | 2 files |
| L1 target size (`--max_bytes_for_level_base`) | 16 MiB |
| Write buffer size | 1 MiB, at most 2 memtables |
| Write-ahead log | disabled |
| Block size | 4,096 B |
| Block cache | 8 MiB |
| Statistics | enabled, reported every 1,000 operations |

At 25,000 keys four to seven L0→L1 compaction jobs fire, depending on how the guest schedules the
flushes, and the data outgrows the 8 MiB block cache (the final L1 SST alone is 9.6 to 16.6 MB), so
the read phase cannot run from the block cache alone.

| Result set | Directories under `m5out/` | Code | Run |
|------------|----------------------------|------|-----|
| v3 | `baseline_v3/`, `pnm_v3/` | tag `capstone`¹ | KVM boot, switch to TIMING at the region of interest; the paper's figures |
| clean | `baseline_clean/`, `pnm_clean/` | tag `capstone`¹ | A second KVM run; `config.ini` byte-identical to v3 |
| post | `baseline_post/`, `baseline_2core_post/`, `pnm_post/` | `main` | Atomic-CPU boot without systemd, checkpoint at `m5 workbegin`, restore on TIMING CPUs; adds the two-core baseline |

¹ The PNM runs' guest binaries were built from other revisions of the RocksDB tools than the
tagged one; see the README's [Results](../../README.md#results).

Results are in the README: [Results](../../README.md#results) for v3 and clean,
[Results with the current code](../../README.md#results-with-the-current-code) for post. A KVM boot
is not deterministic, so v3 and clean differ; each post run repeats exactly from its checkpoint.
The PNM runs' simulated time includes a 0.5 s startup pause, in which `rocksdb_pnm` waits for its
worker inside the measured region.

New runs of the configs write to `m5out/baseline/` and `m5out/pnm/` (`m5out/baseline_2core/` with
two cores; [GEM5_CUSTOMIZATIONS.md](GEM5_CUSTOMIZATIONS.md) lists the suffixes of atomic and
checkpoint runs).
