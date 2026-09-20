# Near-Data Processing for LSM-tree Compaction Acceleration

A full-system study of offloading **RocksDB compaction** to a **Processing-Near-Memory (PNM)**
unit, evaluated on a modified **gem5** simulator. The project asks a single question: *if
LSM-tree compaction runs near memory instead of on the CPU, how much do foreground reads and
writes speed up?*

On the headline workload, moving compaction off the CPU raised write throughput by **+58.6%**
and cut CPU L2 misses by **−64.3%**. A repeat of the same configuration gave **+44.7%** and
**−53.8%**: full-system runs are not bit-reproducible, so read the effect as a range rather
than a single figure. Both runs are committed and both are [below](#results).

Capstone project for Electrical Engineering Comprehensive Design (EEE4160) at Yonsei University,
Spring 2026, with Jaesik Jang. Written up as a paper:
[`paper/pnm-compaction-paper.pdf`](paper/pnm-compaction-paper.pdf).

**Division of work.** This repository is the full-system (FS mode) study, and all of the code in
it is mine: the PNM device model, the RocksDB compaction service and its IPC layer, the guest
kernel module and disk image, the gem5 upstream fixes, and the evaluation. Jaesik Jang worked on
a syscall-emulation (SE mode) variant of the same idea, which is not part of this repository.

---

## What was written for this project

The repository vendors two large upstream codebases so the hardware/software contract stays in
one place. **The project's own code is the 13 files below, about 1,700 lines, plus targeted
changes to twelve upstream files.** Everything else under `gem5/` and `rocksdb/` is upstream:

- `gem5/` is gem5 **v25.1.0.1**.
- `rocksdb/` is RocksDB **main at `e492562`** (2026-05-29, version 11.4.0), without 13 repository
  tooling files (agent instructions and review workflows) that the build does not use.

Every change to an upstream file is committed as a patch against those versions:
[`patches/gem5.patch`](patches/gem5.patch) and [`patches/rocksdb.patch`](patches/rocksdb.patch).

**The simulated hardware**: a new gem5 MMIO device.

| File | Lines | What it does |
|------|-------|--------------|
| [`gem5/src/dev/pnm/pnm_compactor.hh`](gem5/src/dev/pnm/pnm_compactor.hh) | 121 | Register map, class definition, stats |
| [`gem5/src/dev/pnm/pnm_compactor.cc`](gem5/src/dev/pnm/pnm_compactor.cc) | 243 | MMIO read/write, latency model, DMA result write |
| [`gem5/src/dev/pnm/PNMCompactor.py`](gem5/src/dev/pnm/PNMCompactor.py) | 83 | SimObject parameter wrapper |
| [`gem5/src/dev/pnm/SConscript`](gem5/src/dev/pnm/SConscript) | 7 | Build registration |

**The simulated software**: a RocksDB `CompactionService` that offloads jobs.

| File | Lines | What it does |
|------|-------|--------------|
| [`rocksdb/tools/pnm_compaction_service.h`](rocksdb/tools/pnm_compaction_service.h) | 219 | The offloading `CompactionService` implementation |
| [`rocksdb/tools/pnm_unit_main.cc`](rocksdb/tools/pnm_unit_main.cc) | 272 | `pnm_compaction_unit` worker process |
| [`rocksdb/tools/pnm_ipc.h`](rocksdb/tools/pnm_ipc.h) | 55 | Length-prefixed UNIX-socket framing |
| [`rocksdb/tools/db_bench_pnm_main.cc`](rocksdb/tools/db_bench_pnm_main.cc) | 95 | `db_bench` entry point with the service wired in |

**Bringing the two together**: full-system harness and guest driver.

| File | Lines | What it does |
|------|-------|--------------|
| [`gem5/configs/yonsei/run_pnm.py`](gem5/configs/yonsei/run_pnm.py) | 186 | PNM-offloaded full-system run |
| [`gem5/configs/yonsei/run_baseline.py`](gem5/configs/yonsei/run_baseline.py) | 141 | CPU-only control run |
| [`gem5/configs/yonsei/pnm_module.c`](gem5/configs/yonsei/pnm_module.c) | 121 | Guest kernel module exposing `/dev/pnm` |
| [`gem5/configs/yonsei/mount_disk_image.sh`](gem5/configs/yonsei/mount_disk_image.sh) | 144 | Builds the guest disk image |
| [`gem5/configs/yonsei/pnm_module.Makefile`](gem5/configs/yonsei/pnm_module.Makefile) | 9 | Kernel module build |

**Changes to upstream gem5** ([`patches/gem5.patch`](patches/gem5.patch)). Running two cores
through a KVM→TIMING switch with an MMIO device in the loop exposed a class of upstream assertion
failures: packets issued during the KVM phase arrive at the cache or interconnect during the
TIMING phase already partially converted to response format, violating invariants upstream gem5
assumes. Fixing these was a prerequisite for getting any results at all:

| File | Change |
|------|--------|
| `src/mem/cache/mshr.cc` | `needsWritable()` called on response packets in deferred MSHR targets |
| `src/mem/cache/base.cc` | Null `senderState`, evicted-block `LockedRMWWriteReq`, out-of-bounds stats indexing |
| `src/mem/cache/cache.cc` | Software-prefetch assert, uncacheable miss-latency stat, MSHR target response path |
| `src/mem/coherent_xbar.cc` | Assertion on orphan responses with no `routeTo` entry |
| `src/mem/bridge.cc` | x86 IO devices returning unconverted request packets from timing-response callbacks |
| `src/python/gem5/components/boards/kernel_disk_workload.py` | Guest command built from the argument list's repr instead of the arguments |
| `src/python/pybind11/event.cc`, `stats.cc` | `simulate()` releases the Python GIL; the stats dump/reset callbacks reacquire it |
| `.gitignore` | Ignores `disk_images/` and `core`; tracks `m5out/` |

Each gem5 fix is described (symptom, root cause and change) in
[`gem5/docs/GEM5_CUSTOMIZATIONS.md`](gem5/docs/GEM5_CUSTOMIZATIONS.md).

**Changes to upstream RocksDB** ([`patches/rocksdb.patch`](patches/rocksdb.patch)):
`tools/db_bench_tool.cc` gains the `--pnm_offload` and `--pnm_true_offload` flags,
`CMakeLists.txt` adds the `pnm_compaction_unit` and `rocksdb_pnm` targets, and
`db/db_impl/db_impl_secondary.h` makes `CompactWithoutInstallation()` public so the worker can
call it on a persistent secondary DB. See
[`gem5/docs/DB_BENCH_CUSTOMIZATION.md`](gem5/docs/DB_BENCH_CUSTOMIZATION.md).

---

## Why this repository exists

The work spans two codebases that only make sense together, so they are combined here:

| Directory | Role |
|-----------|------|
| [`gem5/`](gem5/) | Modified gem5, the simulated **hardware**: a `PNMCompactor` MMIO device, the full-system run configs, the guest kernel module, and the analysis docs. |
| [`rocksdb/`](rocksdb/) | Modified RocksDB, the simulated **software**: a `CompactionService` that offloads compaction to a near-memory worker process. |

The two halves are coupled by a hardware/software contract: a small **MMIO register map** that is
duplicated, by hand, in both
[`gem5/src/dev/pnm/pnm_compactor.hh`](gem5/src/dev/pnm/pnm_compactor.hh) and
[`rocksdb/tools/pnm_compaction_service.h`](rocksdb/tools/pnm_compaction_service.h). Keeping them in
one repository keeps that contract in sync.

---

## How it works

The design is a **hybrid functional + timing model**. gem5 accounts for *how long* near-memory
compaction would take; a real RocksDB worker process does the *actual* compaction on a separate
core, so its DRAM traffic and cache behavior are simulated faithfully without polluting the main
CPU's caches.

```
  gem5 x86 full-system guest (2 cores, DualChannel DDR4-2400, 3 GiB)
  ┌─────────────────────────────────────────────────────────────────────┐
  │                                                                       │
  │   core 0                              core 1                          │
  │  ┌────────────────────┐    UNIX      ┌────────────────────────────┐   │
  │  │  rocksdb_pnm        │   socket     │  pnm_compaction_unit       │   │
  │  │  (db_bench)         │ ───────────► │  (persistent secondary DB) │   │
  │  │                     │  job input   │  CompactWithoutInstallation│   │
  │  │  PNMCompactionSvc   │ ◄─────────── │                            │   │
  │  └─────────┬───────────┘  job result  └────────────────────────────┘   │
  │            │ MMIO via /dev/pnm (pnm_module.ko)                          │
  │            ▼                                                            │
  │  ┌──────────────────────────────────────────────┐                     │
  │  │ PNMCompactor  (gem5 device @ 0xD0000000)      │  models latency =   │
  │  │  doorbell CMD_SUBMIT → STATUS_DONE after      │  (src+dst)/25GiB/s  │
  │  │  the modeled near-memory delay                │  + 500 ns           │
  │  └──────────────────────────────────────────────┘                     │
  └─────────────────────────────────────────────────────────────────────┘
```

**Per compaction job:**

1. RocksDB schedules a compaction. `PNMCompactionService::Schedule()` serializes the job, sends it
   over a UNIX domain socket (`/tmp/pnm_compaction.sock`) to `pnm_compaction_unit`, and rings the
   gem5 doorbell by writing `CMD_SUBMIT` to the PNM device's MMIO register (through the
   `/dev/pnm` mapping provided by `pnm_module.ko`).
2. The gem5 `PNMCompactor` device models the near-memory cost,
   `(src_bytes + dst_bytes) / 25 GiB/s + 500 ns`, then raises `STATUS_DONE`.
3. Concurrently, `pnm_compaction_unit` performs the real compaction. It keeps a persistent
   secondary DB (`DB::OpenAsSecondary`) so the MANIFEST is parsed once; each job is just
   `TryCatchUpWithPrimary()` + `CompactWithoutInstallation()`.
4. `PNMCompactionService::Wait()` polls the modeled `STATUS`, then reads the result frame back
   over the socket and installs the new SSTs.

Because the worker runs on its own core, **compaction never blocks the RocksDB write thread**.
That concurrency, plus a deduplication pass that shrinks output SSTs, is where the speedups come
from.

---

## Repository layout

```
gem5-pnm-lsm-compaction/
├── README.md                          ← this file
├── patches/                           every change to upstream gem5 and RocksDB, as patches
├── tools/check_results.py             recomputes the README and paper figures from gem5/m5out/
├── paper/                             the write-up
│
├── gem5/                              modified gem5 simulator (v25.1.0.1)
│   ├── src/dev/pnm/                   PNMCompactor device (MMIO latency model)
│   │   ├── pnm_compactor.{hh,cc}      register map + timing model
│   │   ├── PNMCompactor.py            SimObject parameters
│   │   └── SConscript
│   ├── configs/yonsei/                full-system run scripts
│   │   ├── run_baseline.py            CPU-only compaction (control)
│   │   ├── run_pnm.py                 PNM-offloaded compaction
│   │   ├── pnm_module.c               guest kernel module → /dev/pnm
│   │   ├── pnm_module.Makefile
│   │   └── mount_disk_image.sh        builds the guest disk image
│   ├── docs/                          install guide, workloads, analyses
│   └── m5out/                         saved outputs of both run pairs (v3 and the repeat)
│
└── rocksdb/                           modified RocksDB (main at e492562)
    └── tools/
        ├── pnm_compaction_service.h   CompactionService that offloads jobs
        ├── pnm_unit_main.cc           pnm_compaction_unit worker process
        ├── pnm_ipc.h                  length-prefixed UNIX-socket framing
        └── db_bench_pnm_main.cc       db_bench entry point with PNM service wired in
```

---

## Simulated machine

| Component | Configuration |
|-----------|---------------|
| Board / ISA | `X86Board`, x86-64 full system, 3 GHz |
| Boot | KVM (near-native) → switch to **TIMING** CPU at the region of interest |
| Cores | Baseline: 1. PNM: 2, core 0 `rocksdb_pnm`, core 1 `pnm_compaction_unit` |
| Caches | Private L1 (32 KiB I + 32 KiB D) / L2 (512 KiB) |
| Memory | Dual-channel DDR4-2400, 3 GiB |
| Guest | Ubuntu 24.04, Linux 6.8.0-52 |
| PNM device | MMIO @ `0xD0000000`, `process_latency=500ns`, `bandwidth=25GiB/s` (AxDIMM-style) |

---

## Build & run

Full setup is in [`gem5/docs/INSTALL_GUIDE.md`](gem5/docs/INSTALL_GUIDE.md). In short:

```bash
# 1. Build gem5 (the ALL build includes the PNMCompactor device)
cd gem5
scons build/ALL/gem5.opt -j"$(nproc)"

# 2. Build the guest disk image (compiles rocksdb_pnm, pnm_compaction_unit,
#    pnm_module.ko and installs them, plus /sbin/m5 and a NOPASSWD sudoers rule)
./configs/yonsei/mount_disk_image.sh

# 3. Run the control and the PNM-offloaded configurations
./build/ALL/gem5.opt configs/yonsei/run_baseline.py   # ~1.7 h host time, writes m5out/baseline/
./build/ALL/gem5.opt configs/yonsei/run_pnm.py        # ~2.4 h host time, writes m5out/pnm/
```

The scripts locate `gem5/` and `rocksdb/` from their own location, so no paths need editing.

**Prerequisites:** Ubuntu 22.04/24.04 (x86-64), 16 GB RAM (32 GB recommended), ~35 GB free disk,
KVM enabled (`ls /dev/kvm`). Upstream gem5's test data reaches 146 characters of path, so cloning
on Windows into a deep directory needs `git -c core.longpaths=true clone`. The RocksDB benchmark
driver is `db_bench fillrandom,readrandom,waitforcompaction`, 25,000 keys × 1 KiB values,
level-style compaction.

---

## Results

Current workload (**v3**), `gem5/m5out/baseline_v3/` vs `gem5/m5out/pnm_v3/`:

| Metric | Baseline (CPU) | PNM | Change |
|--------|----------------|-----|--------|
| Write throughput | 29,141 ops/s | 46,217 ops/s | **+58.6%** |
| Write latency | 34.3 µs/op | 21.6 µs/op | **−36.9%** |
| Read throughput | 18,264 ops/s | 22,834 ops/s | **+25.0%** |
| Read latency | 54.8 µs/op | 43.8 µs/op | **−20.0%** |
| Compaction wall-clock | 1,595,756 µs | 1,118,829 µs | **−29.9%** |
| Compaction output size | 0.89× input | 0.63× input | **−42% SST size** |
| DRAM read requests | 16.07 M | 14.15 M | **−12.0%** |
| CPU L2 misses (core 0) | 11.06 M | 3.94 M | **−64.3%** |

The dominant benefit is **concurrency**: five L0→L1 jobs ran in parallel on the PNM core without
stalling writes, amplified by a secondary-DB deduplication pass that dropped 8,441 superseded
keys, producing 40–44% smaller output SSTs that cut both read amplification and DRAM traffic.
Full breakdown: [`gem5/docs/comp_v3.md`](gem5/docs/comp_v3.md).

### Repeat run

The same two configurations were run a second time, after a cleanup of `configs/yonsei/` that
changed no benchmark parameter; the `config.ini` files of the two pairs are byte-identical.
`gem5/m5out/baseline_clean/` vs `gem5/m5out/pnm_clean/`:

| Metric | Baseline (CPU) | PNM | Change |
|--------|----------------|-----|--------|
| Write throughput | 29,381 ops/s | 42,523 ops/s | **+44.7%** |
| Write latency | 34.0 µs/op | 23.5 µs/op | **−30.9%** |
| Read throughput | 21,913 ops/s | 24,731 ops/s | **+12.9%** |
| Read latency | 45.6 µs/op | 40.4 µs/op | **−11.4%** |
| Compaction wall-clock | 1,114,831 µs | 953,857 µs | **−14.4%** |
| CPU L2 misses (core 0) | 9.87 M | 4.56 M | **−53.8%** |

The direction is the same and the magnitudes are not. A full-system run boots a real guest under
KVM and then switches to timing mode, so the guest's scheduling decisions differ between runs:
the baseline ran four compaction jobs here against five in the first run, and dropped 7,842
superseded keys against 8,441. That changes how much work compaction does and therefore how much
of it the offload removes. Taken together the two runs put the write-throughput gain between
**+45% and +59%** and the L2-miss reduction between **−54% and −64%**, which is the honest
precision for a two-run sample.

Every figure in both tables except the per-job output sizes (taken from the compaction log in
`comp_v3.md`), and every full-system figure in the paper, can be recomputed from the committed
logs without running gem5. The script needs only Python 3, no packages:

```bash
python tools/check_results.py
```

### Paper vs. this repository

The paper describes the proposed hardware: an NMP unit in the DIMM buffer chip with an FSM, a
2-way comparator and a DMA/retry controller. This repository evaluates that design with the hybrid
model described [above](#how-it-works): the gem5 device charges the near-memory latency, and a
RocksDB worker on a second simulated core performs the actual merge. The paper's full-system
results (Section 5, Figure 5) are computed from the first run pair, `*_v3`:

| Paper, Figure 5 | Value | From the v3 runs |
|-----------------|------:|------------------|
| L2 cache misses | 0.357× | CPU0 L2 misses, 3.94 M / 11.06 M |
| Compaction time | 0.701× | total compaction time, 1,118,829 / 1,595,756 µs |
| Latency | 0.734× | write + read latency, (21.6 + 43.8) / (34.3 + 54.8) µs/op |
| Bandwidth | 1.49× | write + read bandwidth, (45.8 + 14.6) / (28.9 + 11.6) MB/s |
| Memory traffic | 0.895× | DRAM bytes read + written |
| Execution time | 0.735× | write + read phase time, (0.541 + 1.095) / (0.858 + 1.369) s |

Two differences from the paper's text: its Table 1 lists a single-core CPU, which is the baseline
configuration (the PNM configuration adds the second core that runs the worker), and the
system-emulation (SE) results in its Section 4 come from separate experiments whose outputs are
not included here.

---

## Documentation

All in [`gem5/docs/`](gem5/docs/):

| File | Contents |
|------|----------|
| [`INSTALL_GUIDE.md`](gem5/docs/INSTALL_GUIDE.md) | From-scratch build and environment setup |
| [`USEFUL_COMMANDS.md`](gem5/docs/USEFUL_COMMANDS.md) | Running and analyzing simulations |
| [`WORKLOADS.md`](gem5/docs/WORKLOADS.md) | `db_bench` parameters of the committed runs |
| [`GEM5_CUSTOMIZATIONS.md`](gem5/docs/GEM5_CUSTOMIZATIONS.md) | gem5 modifications and extensions |
| [`DB_BENCH_CUSTOMIZATION.md`](gem5/docs/DB_BENCH_CUSTOMIZATION.md) | RocksDB modifications and `db_bench` configuration |
| [`comp_v3.md`](gem5/docs/comp_v3.md) | Full performance analysis (current workload) |
| [`component_sizes.md`](gem5/docs/component_sizes.md) | Dataset / cache / DRAM / PNM size hierarchy |

---

## Licensing

Each component keeps its upstream license, unchanged, in its own subdirectory:

- **`gem5/`**: BSD 3-Clause ([`gem5/LICENSE`](gem5/LICENSE))
- **`rocksdb/`**: dual GPLv2 / Apache 2.0 ([`rocksdb/COPYING`](rocksdb/COPYING),
  [`rocksdb/LICENSE.Apache`](rocksdb/LICENSE.Apache))

A combined binary that links both would be governed by GPLv2 (with which BSD-3 and Apache-2.0 are
compatible); the simulator and the database are *built and run separately*, so no such combined
binary is produced here. See [`LICENSE`](LICENSE).

## Provenance

The project was developed in two separate repositories, one for the gem5 side and one for the
RocksDB side, and combined here. The histories were squashed for publication, so this repository
is a snapshot of the finished work rather than a development log.

Both upstream trees are vendored verbatim at the versions listed
[above](#what-was-written-for-this-project) — including their own tooling files, such as gem5's
`.clang-format` and `.devcontainer/`, which keep upstream's comments and settings untouched.
Every deviation from upstream is in [`patches/`](patches/), and applying those patches to clean
checkouts of the two upstream versions reproduces the twelve changed files exactly.

The simulation outputs under `gem5/m5out/` are committed as gem5 wrote them, which is why their
`config.ini`, `config.json` and `config.dot` still name the container paths of the machine that
produced them (`/workspaces/gem5/...`). They are a record of those runs, not something to run
from: the run scripts locate `gem5/` and `rocksdb/` from their own path, and take the disk image
and output directory from `PNM_DISK_IMAGE` and `PNM_OUTDIR`.
