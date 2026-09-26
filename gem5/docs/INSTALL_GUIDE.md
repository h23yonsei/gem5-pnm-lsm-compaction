# Installation and User Guide: gem5 PNM Compaction Research

This guide walks through setting up and running the full-system gem5 simulation from scratch — from
a bare Ubuntu host to producing comparable results for the baseline and PNM configurations.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Prerequisites](#2-prerequisites)
3. [Repository Setup](#3-repository-setup)
4. [Build gem5](#4-build-gem5)
5. [Build RocksDB and PNM Binaries](#5-build-rocksdb-and-pnm-binaries)
6. [One-Time Disk Image Setup](#6-one-time-disk-image-setup)
7. [Running Simulations](#7-running-simulations)
8. [Analyzing Results](#8-analyzing-results)
9. [Expected Results](#9-expected-results)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Project Overview

What the simulation models, the committed results and the limitations of the capstone
runs are in the [README](../../README.md). This guide covers building and running it.

---

## 2. Prerequisites

### Host System

- **OS:** Ubuntu 22.04 or 24.04 LTS (x86-64). KVM-capable host is strongly recommended for
  reasonable simulation times; without KVM, see [7.4 Without KVM](#74-without-kvm).
- **RAM:** 16 GB minimum (32 GB recommended)
- **Disk:** ~35 GB free space (gem5 build ~15 GB, RocksDB build ~3 GB, disk image ~5 GB)
- **CPU:** At least 8 cores recommended for parallel builds and KVM-accelerated boot

Check KVM availability:

```bash
ls /dev/kvm && echo "KVM available"
# If missing: sudo modprobe kvm_intel (or kvm_amd)
```

### System Packages

```bash
sudo apt-get update
sudo apt-get install -y \
    build-essential git python3 python3-dev python3-pip \
    scons m4 zlib1g-dev libprotobuf-dev protobuf-compiler \
    libgflags-dev libsnappy-dev \
    cmake ninja-build \
    qemu-utils util-linux mount \
    linux-headers-$(uname -r)
```

> **Note:** `linux-headers-$(uname -r)` is required for building the PNM kernel module inside the
> guest disk image via `chroot`. The kernel version inside the guest is pinned to
> `6.8.0-52-generic`; the `mount_disk_image.sh` script handles the chroot build automatically.

### Python Packages

```bash
pip3 install -r $REPO/gem5/requirements.txt
# Or the minimal set:
pip3 install scons pyelftools
```

---

## 3. Repository Setup

Both halves of the project live in one repository:

```bash
git clone https://github.com/h23yonsei/gem5-pnm-lsm-compaction.git
cd gem5-pnm-lsm-compaction
export REPO="$(pwd)"
```

```text
$REPO/
├── gem5/       ← gem5 25.1 with the PNMCompactor device, run configs, guest module, fixes
└── rocksdb/    ← RocksDB 11.4 with the PNM CompactionService and worker process
```

The commands below use `$REPO` for the repository root. The run configs and
`mount_disk_image.sh` locate `gem5/` and `rocksdb/` relative to their own location, so no paths
need editing; `GEM5_DIR`, `ROCKSDB_DIR`, `PNM_DISK_IMAGE` and `PNM_OUTDIR` override them.

---

## 4. Build gem5

From the gem5 repository root:

```bash
cd $REPO/gem5
scons build/ALL/gem5.opt -j$(nproc)
```

This builds the gem5 binary with support for all ISAs and all device models including
`PNMCompactor`. The build takes approximately 15–40 minutes depending on CPU count.

**Output:** `build/ALL/gem5.opt`

If the build fails, check:

```bash
python3 --version    # must be 3.6+
scons --version      # must be 3.0+
g++ --version        # must be GCC 7+ or Clang 6+
```

### Build the m5 Guest Utility

The `m5` binary is a small program that runs inside the simulated guest OS to signal gem5 (reset
stats, mark ROI start/end, exit). Build it separately:

```bash
cd $REPO/gem5/util/m5
scons build/x86/out/m5
```

**Output:** `util/m5/build/x86/out/m5`

---

## 5. Build RocksDB and PNM Binaries

All three binaries (`db_bench`, `rocksdb_pnm`, `pnm_compaction_unit`) are built from the same
RocksDB fork using CMake. They are statically linked so they run inside the minimal guest OS without
shared library dependencies.

```bash
cd $REPO/rocksdb
mkdir -p build_gflags
cd build_gflags

cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DPORTABLE=ON \
    -DWITH_GFLAGS=1 \
    -DWITH_TESTS=OFF \
    -DWITH_TOOLS=ON \
    -DWITH_BENCHMARK_TOOLS=ON \
    -DROCKSDB_BUILD_SHARED=OFF \
    -DWITH_SNAPPY=ON \
    -DWITH_LIBURING=OFF

make -j$(nproc) db_bench rocksdb_pnm pnm_compaction_unit
```

After the CMake build, create static-linked versions (required for the guest):

```bash
CXXFLAGS="-W -Wextra -Wall -pthread -Wsign-compare -Wshadow \
    -Wno-unused-parameter -Wno-unused-variable -Woverloaded-virtual \
    -Wnon-virtual-dtor -Wno-missing-field-initializers -Wno-strict-aliasing \
    -Wno-invalid-offsetof -fno-omit-frame-pointer -momit-leaf-frame-pointer \
    -Werror -fno-builtin-memcmp -O3 -DNDEBUG -fno-rtti \
    -static-libgcc -static-libstdc++"

# Baseline benchmark binary
/usr/bin/c++ $CXXFLAGS \
    CMakeFiles/db_bench.dir/tools/simulated_hybrid_file_system.cc.o \
    CMakeFiles/db_bench.dir/tools/db_bench.cc.o \
    CMakeFiles/db_bench.dir/tools/tool_hooks.cc.o \
    CMakeFiles/db_bench.dir/tools/db_bench_tool.cc.o \
    -o db_bench_static \
    librocksdb.a \
    /usr/lib/x86_64-linux-gnu/libgflags.a \
    /usr/lib/x86_64-linux-gnu/libsnappy.a \
    -lz -lpthread -ldl

# PNM main process (routes compaction to pnm_compaction_unit)
/usr/bin/c++ $CXXFLAGS \
    CMakeFiles/rocksdb_pnm.dir/tools/simulated_hybrid_file_system.cc.o \
    CMakeFiles/rocksdb_pnm.dir/tools/db_bench_pnm_main.cc.o \
    CMakeFiles/rocksdb_pnm.dir/tools/tool_hooks.cc.o \
    CMakeFiles/rocksdb_pnm.dir/tools/db_bench_tool.cc.o \
    -o rocksdb_pnm_static \
    librocksdb.a \
    /usr/lib/x86_64-linux-gnu/libgflags.a \
    /usr/lib/x86_64-linux-gnu/libsnappy.a \
    -lz -lpthread -ldl

# PNM worker process (receives compaction jobs via UNIX socket, drives MMIO)
/usr/bin/c++ $CXXFLAGS \
    CMakeFiles/pnm_compaction_unit.dir/tools/pnm_unit_main.cc.o \
    -o pnm_compaction_unit_static \
    librocksdb.a \
    /usr/lib/x86_64-linux-gnu/libgflags.a \
    /usr/lib/x86_64-linux-gnu/libsnappy.a \
    -lz -lpthread -ldl
```

**Outputs:** `build_gflags/db_bench_static`, `build_gflags/rocksdb_pnm_static`,
`build_gflags/pnm_compaction_unit_static`

> **Tip:** `mount_disk_image.sh` (Section 6) performs all of the above automatically if the static
> binaries are missing. You can skip this section and let the script handle it. It does not rebuild
> binaries that already exist, so after changing the RocksDB sources, delete the affected
> `*_static` files (or rebuild them as above) before running it again.

---

## 6. One-Time Disk Image Setup

This step creates the guest OS disk image with all binaries and the PNM kernel module pre-installed.
It only needs to run once (or again if you rebuild the binaries).

```bash
cd $REPO/gem5/configs/yonsei
chmod +x mount_disk_image.sh
./mount_disk_image.sh
```

**What this script does:**

1. Builds static binaries (if not already built, see Section 5)
2. Builds the `m5` guest utility (if not already built, see Section 4)
3. Copies the base Ubuntu 24.04 image from `~/.cache/gem5/x86-ubuntu-24.04-img-4.0.0` to
   `disk_images/x86-ubuntu-24.04-with-db_bench.img`
4. Loop-mounts the image and installs:
   - `/usr/local/bin/db_bench` (baseline benchmark)
   - `/usr/local/bin/rocksdb_pnm` (PNM main process)
   - `/usr/local/bin/pnm_compaction_unit` (PNM worker)
   - `/sbin/m5` (gem5 guest signalling utility)
5. Adds a passwordless sudoers rule for the `gem5` guest user (needed for `insmod`)
6. Builds and installs the PNM kernel module via `chroot`:
   - Source: `configs/yonsei/pnm_module.c`
   - Output in guest: `/root/pnm_module.ko`

**The base disk image** is downloaded automatically by gem5 on the first simulation run if it is not
already cached. If you have not run gem5 before, trigger the download first:

```bash
cd $REPO/gem5
python3 -c "from gem5.resources.resource import obtain_resource; obtain_resource('x86-ubuntu-24.04-img-4.0.0')"
```

**Output:** `disk_images/x86-ubuntu-24.04-with-db_bench.img` (~4.9 GB)

**Expected runtime:** 5–15 minutes (dominated by the chroot kernel module build).

---

## 7. Running Simulations

Both simulations follow the same pattern: the guest OS boots under KVM at near-native speed, then
gem5 switches to cycle-accurate TIMING mode at the start of the RocksDB benchmark region (ROI).

### 7.1 Baseline Simulation

```bash
cd $REPO/gem5
./build/ALL/gem5.opt configs/yonsei/run_baseline.py
```

**System configuration:**
- 1 CPU core, KVM boot → TIMING ROI
- 3 GiB DDR4-2400 dual-channel
- Per-core L1D/L1I (32 KiB each), L2 (512 KiB)
- Workload: `db_bench fillrandom,readrandom,waitforcompaction --num=25000 --value_size=1024
  --seed=42`

**Output directory:** `m5out/baseline/`

**Expected runtime:** 1.5–2.5 hours (most of it is the TIMING-mode ROI)

**Two-core baseline:** `PNM_BASELINE_CORES=2 ./build/ALL/gem5.opt configs/yonsei/run_baseline.py`
runs the same baseline on two cores, with RocksDB's background flush and compaction threads free
to use the second one, and writes to `m5out/baseline_2core/`. Compared with the PNM run, it shows
whether a dedicated compaction unit does better than a second general-purpose core; the one-core
baseline remains the main comparison.

### 7.2 PNM Simulation

```bash
cd $REPO/gem5
./build/ALL/gem5.opt configs/yonsei/run_pnm.py
```

**System configuration:**
- 2 CPU cores, KVM boot → TIMING ROI
- Same memory and cache hierarchy as baseline
- `PNMCompactor` MMIO device at `0xD0000000` (500 ns latency, 25 GiB/s)
- `rocksdb_pnm` (main process) pinned to core 0 and `pnm_compaction_unit` (compaction worker)
  pinned to core 1; both log the pin they got to the guest console

**Output directory:** `m5out/pnm/`

**Expected runtime:** 2–3 hours

### 7.3 Monitoring Progress

Gem5 prints progress to stdout. Key lines to watch:

```text
# KVM boot phase (fast, a few minutes):
system.pc.com_1.device: [boot messages...]

# ROI start (TIMING mode begins, simulation slows down):
>>> ROI start: switching KVM -> TIMING CPU for detailed simulation

# For PNM only, drain is called first:
>>> ROI start: draining in-flight ops before KVM -> TIMING switch
>>> Drained; switching to TIMING CPU

# Benchmark running (guest output forwarded):
fillrandom   :   34.315 micros/op 29141 ops/sec ...
readrandom   :   54.752 micros/op 18264 ops/sec ...

# ROI end:
>>> ROI end: benchmark complete
>>> Simulation exiting cleanly
Simulation complete.
```

### 7.4 Without KVM

On a host without `/dev/kvm`, boot on gem5's atomic CPU and split each run in two: a boot that saves
a checkpoint at `m5 workbegin`, then a restore of that checkpoint on TIMING CPUs, which runs the
region of interest. `PNM_NO_SYSTEMD=1` boots the guest without systemd, which the atomic CPU would
otherwise take hours to start; the image's init logs in and runs the ROI script directly. The
README's post-capstone results were made this way. From `$REPO/gem5`:

```bash
export PNM_BOOT_CPU=atomic PNM_NO_SYSTEMD=1

# 1. boot and checkpoint at the ROI (m5out/<config>_boot/)
PNM_SAVE_CHECKPOINT=ckpt/baseline ./build/ALL/gem5.opt configs/yonsei/run_baseline.py
PNM_SAVE_CHECKPOINT=ckpt/baseline_2core PNM_BASELINE_CORES=2 \
    ./build/ALL/gem5.opt configs/yonsei/run_baseline.py
PNM_SAVE_CHECKPOINT=ckpt/pnm ./build/ALL/gem5.opt configs/yonsei/run_pnm.py

# 2. restore on TIMING CPUs and run the ROI (m5out/<config>_restored/)
PNM_RESTORE_CHECKPOINT=ckpt/baseline ./build/ALL/gem5.opt configs/yonsei/run_baseline.py
PNM_RESTORE_CHECKPOINT=ckpt/baseline_2core PNM_BASELINE_CORES=2 \
    ./build/ALL/gem5.opt configs/yonsei/run_baseline.py
PNM_RESTORE_CHECKPOINT=ckpt/pnm ./build/ALL/gem5.opt configs/yonsei/run_pnm.py
```

The boots took 15–30 minutes and the restores 60–75 minutes each on the host used for the
post-capstone runs, and each process holds about 3.4 GB. A checkpoint is about 30 MB. The simulation
is deterministic: restoring each of the three checkpoints a second time reproduced every simulated
statistic and the console output byte for byte.

Use checkpoints for the PNM configuration: switching it from the atomic CPU to TIMING at
`workbegin` (`PNM_BOOT_CPU=atomic` without a checkpoint) stops gem5 with a cache assertion
(`BaseCache::satisfyRequest`). The baselines switch cleanly, but their caches are then warm at the
start of the ROI, unlike after a KVM boot or a restore.

---

## 8. Analyzing Results

All results are in the `m5out/` directories. The two key output files are:

- `stats.txt` — gem5 microarchitectural counters (IPC, cache, memory)
- `board.pc.com_1.device` — RocksDB application output (throughput, latency, compaction stats)

### 8.1 Application Throughput

```bash
# Baseline
grep "fillrandom\|readrandom" m5out/baseline/board.pc.com_1.device \
    | grep -v "thread\|benchmarks\|DB path"

# PNM
grep "fillrandom\|readrandom" m5out/pnm/board.pc.com_1.device \
    | grep -v "thread\|benchmarks\|DB path"
```

### 8.2 CPU IPC

```bash
# Baseline (1 core)
grep "switch.*\.ipc " m5out/baseline/stats.txt | head -1

# PNM (2 cores: switch0 = main RocksDB, switch1 = PNM unit)
grep "switch[01].*\.ipc " m5out/pnm/stats.txt | head -4
```

### 8.3 L1D Cache (CPU 0)

```bash
grep "l1d-cache-0\.demandMisses::total\|l1d-cache-0\.demandAccesses::total\|l1d-cache-0\.demandMissRate::total\|l1d-cache-0\.demandAvgMissLatency::total" \
    m5out/baseline/stats.txt | head -4

grep "l1d-cache-0\.demandMisses::total\|l1d-cache-0\.demandAccesses::total\|l1d-cache-0\.demandMissRate::total\|l1d-cache-0\.demandAvgMissLatency::total" \
    m5out/pnm/stats.txt | head -4
```

### 8.4 L2 Cache (CPU 0)

```bash
grep "l2-cache-0\.demandMisses::total\|l2-cache-0\.demandHits::total\|l2-cache-0\.demandMissRate::total" \
    m5out/baseline/stats.txt | head -3

grep "l2-cache-0\.demandMisses::total\|l2-cache-0\.demandHits::total\|l2-cache-0\.demandMissRate::total" \
    m5out/pnm/stats.txt | head -3
```

### 8.5 DRAM Traffic

```bash
grep "mem_ctrl.*\.readReqs\b" m5out/baseline/stats.txt | head -2
grep "mem_ctrl.*\.readReqs\b" m5out/pnm/stats.txt | head -2
```

### 8.6 RocksDB Compaction Internals

```bash
grep "compaction\.times\.micros\|compaction\.key\.drop\.new\|numfiles\.in\.single" \
    m5out/baseline/board.pc.com_1.device

grep "compaction\.times\.micros\|compaction\.key\.drop\.new\|numfiles\.in\.single" \
    m5out/pnm/board.pc.com_1.device
```

---

## 9. Expected Results

Compare your `stats.txt` and console output with the committed runs in `m5out/`, and the
figures with the README's [Results](../../README.md#results) (capstone runs) and
[Results with the current code](../../README.md#results-with-the-current-code). A KVM boot
is not bit-reproducible, so expect the direction and order of magnitude, not the digits;
restoring the same checkpoint twice gives identical statistics.

---

## 10. Troubleshooting

**Disk image not found:**
```text
CRITICAL ERROR: Custom disk image not found at $REPO/gem5/disk_images/x86-ubuntu-24.04-with-db_bench.img
```
Run `configs/yonsei/mount_disk_image.sh` first. If the base image is also missing, run the resource
download command from Section 6.

---

**Base image not cached:**
```text
ERROR: Source image not found at /root/.cache/gem5/x86-ubuntu-24.04-img-4.0.0
```
Trigger the download by running gem5 once with any config that uses the same kernel/image resource,
or use the Python snippet in Section 6.

---

**Kernel module fails to load in guest:**
```text
[gem5] WARNING: pnm_module.ko failed to load — MMIO will be bypassed
```
The module was built against a different kernel version. The guest OS requires kernel
`6.8.0-52-generic`. Re-run `mount_disk_image.sh` with matching kernel headers on the host. Verify
with:
```bash
sudo chroot /mnt/gem5img uname -r   # should print 6.8.0-52-generic
```

---

**Simulation hangs at CPU switch:**

Symptom: gem5 prints the drain/switch messages and then appears to hang indefinitely.

Cause: The `PNMCompactor` had an in-flight completion event that was not drained before the switch.
This is handled by `m5.drain()` in `workbegin_handler` in `run_pnm.py`. If you modify the config,
ensure `m5.drain()` is always called before `simulator.switch_processor()` when a MMIO device is
present.

---

**MSHR assertion failure:**
```text
panic: MSHR target is not a request
```
This indicates a gem5 cache bug triggered during multi-core KVM→TIMING switch. The five core bug-fix
patches in this repository (`src/mem/cache/mshr.cc`, `src/mem/cache/base.cc`,
`src/mem/cache/cache.cc`, `src/mem/coherent_xbar.cc`, `src/mem/bridge.cc`) fix this. If you are
merging with upstream gem5, ensure these patches are applied. See
[docs/GEM5_CUSTOMIZATIONS.md](GEM5_CUSTOMIZATIONS.md) for details.

---

**Path mismatch errors:**

Config scripts and `mount_disk_image.sh` use hardcoded paths:
- gem5 at `$REPO/gem5/`
- rocksdb at `$REPO/rocksdb/`

If you cloned elsewhere, update the path constants at the top of:
- `configs/yonsei/run_baseline.py` (line: `DISK_IMAGE = ...`)
- `configs/yonsei/run_pnm.py` (line: `DISK_IMAGE = ...`)
- `configs/yonsei/mount_disk_image.sh` (lines: `DEST_DIR`, `BINARY`, etc.)

---

**gem5 build fails (SCons errors):**

Clean the build cache and retry:
```bash
cd $REPO/gem5
rm -rf build/ALL/
scons build/ALL/gem5.opt -j$(nproc)
```
