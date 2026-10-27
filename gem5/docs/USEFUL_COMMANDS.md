# Useful Commands

Quick reference for running, monitoring, and analyzing gem5 PNM simulations.

---

## Build

```bash
# Build gem5 (ALL variant includes PNMCompactor)
cd $REPO/gem5
scons build/ALL/gem5.opt -j$(nproc)

# Check KVM availability
ls /dev/kvm && echo "KVM available" || echo "KVM not available"
sudo modprobe kvm_intel   # or kvm_amd

# Build m5 guest utility
cd $REPO/gem5/util/m5
scons build/x86/out/m5
```

---

## Build RocksDB binaries

```bash
cd $REPO/rocksdb
mkdir -p build_gflags && cd build_gflags

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

> Then run `mount_disk_image.sh` to relink all three binaries with the static libraries
> ([INSTALL_GUIDE.md](INSTALL_GUIDE.md) section 5 shows the relink step) and install them into the
> disk image. It skips the relink while the `*_static` files exist, so delete them after changing
> the sources.

---

## Run simulations

```bash
# Clear the previous run's outputs (the committed result sets are the *_v3, *_clean and *_post
# directories; leave those in place)
rm -rf $REPO/gem5/m5out/baseline $REPO/gem5/m5out/pnm

# Run baseline
cd $REPO/gem5
./build/ALL/gem5.opt configs/yonsei/run_baseline.py

# Run PNM
cd $REPO/gem5
./build/ALL/gem5.opt configs/yonsei/run_pnm.py
```

In the capstone runs the region of interest took 1.6–1.7 h of host time for the baseline and
2.2–2.4 h for PNM (`hostSeconds` in `stats.txt`); the KVM boot adds a few minutes.

---

## Monitor a running simulation

```bash
# Stream RocksDB application output (throughput, compaction events) from the guest console.
# The run scripts write to m5out/baseline/, m5out/baseline_2core/ or m5out/pnm/ (or PNM_OUTDIR).
tail -f $REPO/gem5/m5out/baseline/board.pc.com_1.device
tail -f $REPO/gem5/m5out/pnm/board.pc.com_1.device

# Count the compaction jobs finished so far (stats.txt is written only at the ROI's end)
grep -c "^\[compaction\] done" $REPO/gem5/m5out/pnm/board.pc.com_1.device

# Check how long the host has been running the sim
ps -C gem5.opt -o pid,etime,args

# Kill a running simulation
pkill -f gem5.opt
```

---

## Compare results after simulation

```bash
# --- Application throughput ---
grep "fillrandom\|readrandom" m5out/baseline/board.pc.com_1.device \
    | grep -v "thread\|benchmarks\|DB path"
grep "fillrandom\|readrandom" m5out/pnm/board.pc.com_1.device \
    | grep -v "thread\|benchmarks\|DB path"

# --- CPU IPC (TIMING phase) ---
grep "switch.*\.ipc " m5out/baseline/stats.txt | head -2
grep "switch[01].*\.ipc " m5out/pnm/stats.txt | head -4

# --- L2 cache misses (CPU0) ---
grep "l2-cache-0\.demandMisses::total" m5out/baseline/stats.txt
grep "l2-cache-0\.demandMisses::total" m5out/pnm/stats.txt

# --- DRAM read requests ---
grep "mem_ctrl.*\.readReqs\b" m5out/baseline/stats.txt
grep "mem_ctrl.*\.readReqs\b" m5out/pnm/stats.txt

# --- Simulated time ---
grep "^simSeconds" m5out/baseline/stats.txt | head -1
grep "^simSeconds" m5out/pnm/stats.txt | head -1

# --- PNM device stats ---
grep "pnm_compactor\." m5out/pnm/stats.txt

# --- RocksDB internal compaction stats ---
grep "compaction\.times\.micros\|compaction\.key\.drop\.new\|numfiles\.in\.single" \
    m5out/baseline/board.pc.com_1.device
grep "compaction\.times\.micros\|compaction\.key\.drop\.new\|numfiles\.in\.single" \
    m5out/pnm/board.pc.com_1.device

# --- PNM worker: pins, output compression, per-job records and bytes (current code only) ---
grep "\[rocksdb_pnm\] pinned\|\[pnm_unit\] pinned\|output compression\|records in=" \
    m5out/pnm/board.pc.com_1.device
```

For the two-core baseline, use `m5out/baseline_2core/` and take the first four `ipc` lines, as for
PNM. For a run restored from a checkpoint, use `m5out/<config>_restored/` and grep `start` instead
of `switch`: the region of interest runs on the starting cores.

---

## Disk image

```bash
# Download the base Ubuntu 24.04 image (first time only)
cd $REPO/gem5
./build/ALL/gem5.opt -c "
from gem5.resources.resource import obtain_resource
obtain_resource('x86-ubuntu-24.04-img', resource_version='4.0.0').get_local_path()
"

# Build the custom disk image with db_bench + PNM binaries installed
cd $REPO/gem5/configs/yonsei
chmod +x mount_disk_image.sh
./mount_disk_image.sh

# Check image exists and size
ls -lh $REPO/gem5/disk_images/x86-ubuntu-24.04-with-db_bench.img
```

---

## Check the committed results

```bash
# Recompute the results the README and the paper quote from the committed simulation logs
python $REPO/tools/check_results.py
```
