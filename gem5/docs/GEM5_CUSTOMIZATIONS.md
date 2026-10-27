# gem5 Customizations — PNM Research Fork

This document describes every change made to the upstream gem5 simulator for the Yonsei University
PNM (Processing-Near-Memory) research project. The goal of the research is to evaluate the
performance benefit of offloading RocksDB LSM-tree compaction to a near-memory accelerator,
eliminating the CPU and memory-bandwidth cost of compaction on the host processor.

**Simulation strategy:** The system boots under KVM (fast, near-native speed) to reach the RocksDB
workload start point, then switches to gem5's TIMING CPU (`TimingSimpleCPU`: in order, with every
memory access timed through the caches and DRAM) for the region of interest (ROI). Without KVM, it
boots on the atomic CPU, checkpoints at the ROI and restores the checkpoint on TIMING CPUs. Only
the benchmark phase accumulates stats.

---

## 1. New device: PNMCompactor (`src/dev/pnm/`)

The core addition is a new gem5 SimObject that models a near-memory compaction accelerator.

### Files

| File | Purpose |
|------|---------|
| `src/dev/pnm/pnm_compactor.hh` | Class definition, MMIO register offset constants, `PNMStats` subclass |
| `src/dev/pnm/pnm_compactor.cc` | MMIO read/write handling, latency model, optional `physProxy` result write |
| `src/dev/pnm/PNMCompactor.py` | SimObject Python wrapper; exposes parameters to the config scripts |
| `src/dev/pnm/SConscript` | Build entry — compiles `pnm_compactor.cc` into the gem5 binary |

### MMIO register map

Base address: `0xD0000000`, region size: `0x100` bytes. The device sits on the board's IO bus, which
`X86Board`'s bridge reaches from the memory bus for addresses in `0xC0000000`–`0xFFFF0000`.

| Offset | Name | Access | Description |
|--------|------|--------|-------------|
| `+0x00` | `SRC_BYTES` | WO | Input (source) byte count for the pending compaction job |
| `+0x08` | `DST_BYTES` | WO | Output (destination) byte count |
| `+0x10` | `CMD` | WO | `1` = SUBMIT job, `2` = RESET device |
| `+0x14` | `STATUS` | RO | bit 0 = BUSY, bit 1 = DONE, bit 2 = ERROR |
| `+0x18` | `BYTES_PROC` | RO | Cumulative bytes processed (across all jobs) |
| `+0x20` | `JOBS_DONE` | RO | Cumulative jobs completed |

### Latency model

When the guest writes CMD = SUBMIT, the device schedules a completion event after:

```text
completion_latency = (SRC_BYTES + DST_BYTES) / bandwidth + process_latency
```

Configured parameters (as set in `run_pnm.py`): `bandwidth = 25 GiB/s`, `process_latency = 500 ns`
(AxDIMM-style model: DDR4 round-trip + buffer-chip decode overhead). These match the SimObject
defaults.

These are configurable in the SimObject Python interface and can be overridden at instantiation
time.

gem5 hands a `MemoryBandwidth` parameter to C++ as ticks per byte (about 37.3 for 25 GiB/s), so the
device multiplies the byte count by it. The capstone code divided by it instead, which made the
bandwidth term about 1,400 times too small: in the capstone runs the device raised DONE 0.6–1.4 µs
after each submit, where the formula gives 0.15–1.3 ms. Their timing is unaffected, since the
worker's own compaction (73–365 ms per job in `pnm_v3`) decides when each job completes.

### Two operating modes

- **Latency-only (default, `use_dma = False`):** The device only schedules the completion event. No
  memory is written. STATUS transitions BUSY → DONE when the event fires.
- **DMA mode (`use_dma = True`):** After the completion event fires, a 16-byte result record is
  written to `dma_addr` (default `0x80000000`) via `physProxy.writeBlob()`. Layout:
  `[bytesProcessed:8B][jobsCompleted:4B][pad:4B]` (little-endian, cumulative across all jobs). This
  uses the functional memory port rather than the coherent DMA port, avoiding assertion failures
  caused by in-flight DMA packets during the KVM→TIMING CPU switch. `run_pnm.py` enables this mode
  at the default address, which lies inside the 3 GiB of RAM the guest kernel manages; the guest is
  not told to reserve it, so each record overwrites 16 bytes of whichever page the kernel has placed
  there. Nothing in the guest reads the record, and the latency-only mode avoids the write.

### Drain support

`PNMCompactor` implements `DrainState drain()`. Before the config script switches the CPU from KVM
to TIMING, it drains the system, and the device reports itself drained only once any in-flight
`onComplete` event has fired. This prevents a KVM-issued job from firing its completion event
during the TIMING phase and corrupting stats.

### Statistics

Tracked under the device's stats group:

| Stat | Description |
|------|-------------|
| `bytesProcessed` | Total bytes (src + dst) passed through the accelerator |
| `jobsCompleted` | Total compaction jobs submitted and completed |

---

## 2. Simulation configs (`configs/yonsei/`)

### `run_baseline.py`

Full-system x86 simulation running `db_bench`, built from the patched RocksDB but without the PNM
flags, so compaction runs on the host. Serves as the performance reference.

| Parameter | Value |
|-----------|-------|
| CPU cores | 1, KVM (boot) → TIMING (ROI); 2 with `PNM_BASELINE_CORES=2` |
| Boot options | `PNM_BOOT_CPU=atomic` boots on the atomic CPU instead of KVM; `PNM_NO_SYSTEMD=1` boots the guest without systemd; `PNM_SAVE_CHECKPOINT=<dir>` checkpoints at `workbegin` and stops; `PNM_RESTORE_CHECKPOINT=<dir>` restores on TIMING CPUs, with no switch |
| ISA | x86\_64 |
| Memory | 3 GiB DDR4-2400, dual-channel |
| L1 cache | 32 KiB I-cache + 32 KiB D-cache (private) |
| L2 cache | 512 KiB (private) |
| Workload | `db_bench fillrandom` (25,000 keys) → `readrandom` → `waitforcompaction` |
| ROI markers | `m5 workbegin` / `m5 workend` magic ops, issued by the guest's `/sbin/m5` |
| Drain | With two cores only, `m5.drain()` before the switch, as in `run_pnm.py` |
| Output dir | `m5out/baseline/`, or `m5out/baseline_2core/` with two cores, with `_atomic`, `_boot` or `_restored` appended for an atomic boot, a checkpointing boot or a restore (override with `PNM_OUTDIR`) |

The guest calls `m5 resetstats` **before** `workbegin` (to clear boot-phase stats), then `m5
workbegin` / `m5 workend` bracket the ROI, followed by `m5 dumpresetstats` and `m5 exit`. The first
statistics dump therefore covers only the benchmark phase.

### `run_pnm.py`

Identical to `run_baseline.py` except:

| Parameter | Value |
|-----------|-------|
| CPU cores | 2 — `rocksdb_pnm` on core 0 and `pnm_compaction_unit` (started by `rocksdb_pnm`) on core 1; not pinned to separate cores in the capstone runs |
| PNMCompactor | Attached to x86 IO bus at `0xD0000000`; `bandwidth = 25 GiB/s`, `process_latency = 500 ns` (AxDIMM-style), `use_dma = True` |
| Drain | Config calls `m5.drain()` in `workbegin_handler` before `simulator.switch_processor()` |
| Boot options | As in `run_baseline.py`. Switching this configuration from the atomic CPU to TIMING stops gem5 with a cache assertion (`BaseCache::satisfyRequest`), so without KVM it runs through a checkpoint |
| Output dir | `m5out/pnm/`, with the same suffixes as `run_baseline.py` (override with `PNM_OUTDIR`) |

`rocksdb_pnm` is a patched build of RocksDB that intercepts the compaction path: instead of
compacting in its own process, it sends each job to `pnm_compaction_unit` over a UNIX socket, rings
the device's doorbell through `/dev/pnm` (the MMIO interface), and waits on the socket.
`pnm_compaction_unit` is a helper process running beside `rocksdb_pnm` and stands in for the
near-memory compute element: it reads the input SSTs from the database directory, writes the
output SSTs, polls the device's STATUS until DONE, and returns the result over the socket.

### `mount_disk_image.sh`

Automates building the guest disk image (`disk_images/x86-ubuntu-24.04-with-db_bench.img`, ~4.9 GB,
Ubuntu 24.04). Steps:

1. Build `db_bench`, `rocksdb_pnm` and `pnm_compaction_unit` with gflags support and relink them
   with the static libraries, unless they already exist
2. Build the `m5` guest utility with `workbegin`/`workend` support, unless it already exists
3. Copy the base image and loop-mount its root partition
4. Copy the three binaries to `/usr/local/bin/` and `m5` to `/sbin/` inside the image
5. Configure passwordless sudo for the gem5 user
6. Build `pnm_module.ko` inside the image (`chroot`) against the guest's 6.8.0-52-generic headers
   and install it as `/root/pnm_module.ko`

---

## 3. Guest kernel module (`configs/yonsei/pnm_module.c`)

A GPL-2.0 Linux kernel module that provides userspace access to the PNMCompactor MMIO region.

- Registers `/dev/pnm` as a character device
- The `mmap` file operation maps the 256-byte MMIO window at `0xD0000000` into userspace with
  `pgprot_noncached` page attributes — critical so that polling the STATUS register does not read a
  stale cached value
- Eliminates the need for `/dev/mem` access; `rocksdb_pnm` and `pnm_compaction_unit` open
  `/dev/pnm` and mmap it directly
- Compiled by `configs/yonsei/pnm_module.Makefile` against the guest kernel's 6.8.0-52-generic
  headers, inside the disk image

---

## 4. gem5 core bug fixes

The five memory-system files below were patched to fix assertion failures and crashes that appear
when running multi-core KVM→TIMING simulations with MMIO devices. The underlying cause in every
case: packets issued during the KVM phase (atomic request packets) arrive at the cache or
interconnect during the TIMING phase already partially converted to response format, violating
assumptions baked into upstream gem5.

### `src/mem/cache/mshr.cc`

**`TargetList::updateFlags()`** A deferred MSHR target can be a `WritebackAck` response when a
writeback completes while the MSHR is still in service. Upstream code calls `pkt->needsWritable()`
on every target without checking whether it is a request. `needsWritable()` asserts on response
packets.
- Fix: guard with `pkt->isRequest()` before calling `needsWritable()`.

**`promoteReadable()`** Same class of bug — the promotable predicate checks `needsWritable()`
without confirming the packet is a request. Response packets in deferred targets must be treated as
promotable (return `true` early) rather than having their writable bit inspected.
- Fix: guard with `pkt->isRequest()`; return `true` immediately for response packets.

### `src/mem/cache/base.cc`

**Null `senderState` in `recvTimingResp()`** During the mode switch, some synthesized/orphaned
response packets arrive with no `senderState`. Upstream code unconditionally dereferences it.
- Fix: drop packets with null `senderState`; the real response will eventually arrive and clean up
  the MSHR.

**`LockedRMWWriteReq` with evicted block** If a cache block is evicted during the KVM→TIMING switch,
a pending `LockedRMWWriteReq` for that block asserts because it expects the block to be present.
- Fix: if the block is missing, demote the packet to a plain `WriteReq` and handle it as a normal
  miss.

**Stats recording for response targets** Hit/miss stats are stored in a vector indexed by packet
command. Response packets have different command values than requests; indexing the stats vector
with a response command causes an out-of-bounds access.
- Fix: guard stats recording behind `initial_tgt->pkt->isRequest()`.

**Answered target in `sendMSHRQueuePacket()`** An MSHR target that is forwarded rather than
fetched as a block (an uncacheable access, for instance) can already have been answered while its
send was queued for retry, so the packet copied for the request is a stale response. A `WriteResp`
would never pop the MSHR's sender state, leaving the MSHR in service forever; a `ReadResp` pops it
and lets `serviceMSHRTargets()` clean it up.
- Fix: re-issue such a target as a fresh `ReadReq` instead of copying the stale packet.

### `src/mem/cache/cache.cc`

**Software prefetch path (`needsWritable` assert)** The software prefetch handling calls
`needsWritable()` without an `isRequest()` guard. Larger workloads (25,000 keys) reach this code
path more frequently and reliably trigger the assertion.
- Fix: guard with `isRequest()` before the `needsWritable()` call (~line 745).

**`isUncacheable()` miss-latency guard** MMIO writes that are orphaned into the MSHR during the mode
switch have no cacheable miss latency to record. Upstream unconditionally updates the miss-latency
stat.
- Fix: skip the stat when `isUncacheable()` is true (~line 815).

**Data-copy loop in `serviceMSHRTargets()`** When iterating MSHR targets, a target that is already a
response packet must be skipped — its data was already handled. Additionally, byte-level copies
between a block-aligned response and a sub-block target need bounds checking.
- Fix: check `!tgt_pkt->isRequest()` to skip already-response targets; add defensive bounds check
  for overlapping byte copies.

**`copyResponderFlags()` guard**
Called unconditionally on every target; asserts on response packets.
- Fix: guard with `isRequest()` (~line 897).

**Response path restructure (~lines 901–933)**
Multiple related issues consolidated:
- Break early if the target packet is already a response (avoids use-after-free from
  `makeTimingResponse()`)
- Check `needsResponse()` before calling `makeTimingResponse()` — `InvalidateReq`, `CleanEvict`, and
  similar housekeeping packets must not receive ACKs
- A target that is already a response is the one `base.cc` re-issued as a fresh `ReadReq` (above):
  its round trip has kept the MSHR alive until this point, and it is discarded here rather than
  delivered to the CPU a second time.

### `src/mem/coherent_xbar.cc`

**Orphan response assertion** Upstream asserts when a response packet arrives at the crossbar with
no `routeTo` entry (meaning the crossbar never saw the original request). This can happen when a
device synthesizes a response without going through the normal request path, particularly during
bridge/device misbehavior at the KVM→TIMING boundary.
- Fix: replace the assertion with a defensive packet drop, traced under the `CoherentXBar` debug
  flag.

### `src/mem/bridge.cc`

**`BridgeRequestPort::recvTimingResp()` (~lines 135–148)** Some x86 IO devices return the original
request packet from their timing response callback instead of calling `makeAtomicResponse()` first.
Upstream asserts that the received packet is a response.
- Fix: if `pkt->isRequest() && pkt->needsResponse()`, attempt `pkt->makeResponse()` to convert it;
  discard the packet if it cannot be converted.

**`BridgeResponsePort::trySendTiming()` (~lines 310–334)**
Same class of issue on the response port side — dequeued packets may be unconverted request packets.
- Fix: attempt `makeResponse()` conversion; dequeue and discard unconvertible packets, reschedule
  the send event, and handle retry requests after cleanup.

### Python layer

Smaller changes to three files outside the memory system:

**`src/python/gem5/components/boards/kernel_disk_workload.py`**
Upstream builds the guest command as `f"./myapp {args}"`, which inserts the Python list's
representation (`['a', 'b']`) into the shell command instead of the arguments.
- Fix: join the arguments with spaces.

**`src/python/pybind11/event.cc` and `src/python/pybind11/stats.cc`**
`m5.simulate()` now releases the Python GIL while the C++ event loop runs
(`py::call_guard<py::gil_scoped_release>()`). The statistics dump and reset callbacks run inside
that loop (for example on the guest's `m5 dumpresetstats`), so `pythonDump()` and `pythonReset()`
reacquire the GIL (`py::gil_scoped_acquire`) before calling into Python.

Besides these, `.gitignore` ignores `disk_images/` and `core` and tracks `m5out/`, where the
committed runs are. The complete set of changes to upstream gem5 files is committed as
[`patches/gem5.patch`](../../patches/gem5.patch), against gem5 v25.1.0.1.

---

## 5. Simulation outputs (`m5out/`)

| Directory | Config | Workload |
|-----------|--------|---------|
| `m5out/baseline_v3/` | `run_baseline.py` | `fillrandom` 25,000 keys + `readrandom` + `waitforcompaction` (results in the README) |
| `m5out/pnm_v3/` | `run_pnm.py` | same workload, with PNMCompactor offload (results in the README) |
| `m5out/baseline_clean/`, `m5out/pnm_clean/` | same configs | a second run of the same workload |
| `m5out/baseline_post/`, `m5out/baseline_2core_post/`, `m5out/pnm_post/` | both configs, current code | the same workload from a checkpoint; one-core and two-core baselines (results in the README) |

See [WORKLOADS.md](WORKLOADS.md) for how each set was run. Each directory contains `config.ini`,
`config.json`, `stats.txt` and `board.pc.com_1.device`, the guest's serial console (db_bench's
results and statistics, and the compaction log); the capstone runs also keep gem5's `config.dot`
renderings and `citations.bib`. The first statistics dump in `stats.txt` covers only the ROI
(`workbegin` to `workend`).

---

## 6. Related documentation

- [WORKLOADS.md](WORKLOADS.md) — the benchmark workload and the committed result sets
