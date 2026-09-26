// pnm_compaction_unit — the PNM near-memory compaction worker.
//
// Uses a persistent secondary DB (DB::OpenAsSecondary) so the expensive
// MANIFEST parse happens once at startup.  Each job calls:
//   TryCatchUpWithPrimary()          — fast incremental MANIFEST sync
//   CompactWithoutInstallation()     — compaction only, no DB re-open
//
// CompactWithoutInstallation() writes its output with the secondary
// instance's own column-family options, so the secondary is opened with the
// primary's options, loaded from the primary's latest OPTIONS file. Opening it
// with default Options would compress the output with Snappy even when the
// primary runs with compression off.
//
// Wire protocol (all over UNIX SOCK_STREAM with length-prefixed frames):
//   Request  frame: [db_name | job_id_str | compaction_service_input]
//                    each sub-field is a uint32-LE-length-prefixed string
//   Response frame: [status_byte (0=ok)] + [CompactionServiceResult string]

#include <fcntl.h>
#include <sched.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "db/compaction/compaction_job.h"
#include "db/db_impl/db_impl_secondary.h"
#include "rocksdb/convenience.h"
#include "rocksdb/db.h"
#include "rocksdb/options.h"
#include "rocksdb/utilities/options_util.h"
#include "tools/pnm_ipc.h"

using ROCKSDB_NAMESPACE::ColumnFamilyDescriptor;
using ROCKSDB_NAMESPACE::ColumnFamilyHandle;
using ROCKSDB_NAMESPACE::CompactionServiceInput;
using ROCKSDB_NAMESPACE::CompactionServiceResult;
using ROCKSDB_NAMESPACE::ConfigOptions;
using ROCKSDB_NAMESPACE::DB;
using ROCKSDB_NAMESPACE::DBImplSecondary;
using ROCKSDB_NAMESPACE::DBOptions;
using ROCKSDB_NAMESPACE::OpenAndCompactOptions;
using ROCKSDB_NAMESPACE::Options;
using ROCKSDB_NAMESPACE::Status;

// MMIO register constants — must match pnm_compactor.hh
static constexpr uint32_t kPNMOffStatus  = 0x14;
static constexpr uint32_t kPNMStatusDone = (1u << 1);
static constexpr uint32_t kPNMStatusErr  = (1u << 2);
static constexpr size_t   kPNMSize       = 0x100;
// Mapped once in main(); nullptr if /dev/pnm is unavailable.
static volatile uint8_t* g_mmio_base = nullptr;

// Persistent secondary DB — opened once, reused across all compaction jobs.
// Its column-family handles are kept for the life of the process, which ends
// when rocksdb_pnm terminates it.
static const char* kSecondaryPath = "/tmp/pnm_secondary";
static DBImplSecondary* g_secondary_db = nullptr;
static std::vector<ColumnFamilyHandle*> g_cf_handles;
// Serialize compactions: secondary has one output dir; one job at a time.
static std::mutex g_compact_mutex;

// Parse a sub-field from a compound frame (4-byte LE length prefix + data).
static bool parse_field(const char* buf, size_t buf_len, size_t* pos,
                        std::string* out) {
  if (*pos + 4 > buf_len) return false;
  uint32_t len = 0;
  memcpy(&len, buf + *pos, 4);
  *pos += 4;
  if (*pos + len > buf_len) return false;
  out->assign(buf + *pos, len);
  *pos += len;
  return true;
}

// Name of a compression type, for the log.
static std::string compression_name(ROCKSDB_NAMESPACE::CompressionType type) {
  std::string name;
  if (!ROCKSDB_NAMESPACE::GetStringFromCompressionType(&name, type).ok())
    name = "type " + std::to_string(static_cast<int>(type));
  return name;
}

// Open the secondary DB on the first call; subsequent calls are no-ops.
// Caller MUST hold g_compact_mutex.
static Status ensure_secondary(const std::string& db_path) {
  if (g_secondary_db) return Status::OK();

  mkdir(kSecondaryPath, 0755);

  // Use the primary's own options, so compaction output is written the way
  // the primary would write it (compression, block size, table format).
  DBOptions db_opts;
  std::vector<ColumnFamilyDescriptor> cf_descs;
  Status s = ROCKSDB_NAMESPACE::LoadLatestOptions(ConfigOptions(), db_path,
                                                  &db_opts, &cf_descs);
  if (!s.ok()) {
    fprintf(stderr, "[pnm_unit] WARNING: cannot load the primary's options (%s); "
                    "compacting with default Options, whose compression is %s\n",
            s.ToString().c_str(),
            compression_name(Options().compression).c_str());
    Options opts;
    db_opts = DBOptions(opts);
    cf_descs = {ColumnFamilyDescriptor(ROCKSDB_NAMESPACE::kDefaultColumnFamilyName,
                                       ROCKSDB_NAMESPACE::ColumnFamilyOptions(opts))};
  }
  db_opts.max_open_files = -1;

  std::unique_ptr<DB> db;
  s = DB::OpenAsSecondary(db_opts, db_path, kSecondaryPath, cf_descs,
                          &g_cf_handles, &db);
  if (!s.ok()) return s;

  g_secondary_db = static_cast<DBImplSecondary*>(db.release());
  for (const auto& cf : cf_descs) {
    if (cf.name == ROCKSDB_NAMESPACE::kDefaultColumnFamilyName)
      fprintf(stderr, "[pnm_unit] secondary DB opened at %s (path=%s), "
                      "output compression %s\n",
              db_path.c_str(), kSecondaryPath,
              compression_name(cf.options.compression).c_str());
  }
  return Status::OK();
}

// Handle one compaction job from an accepted socket connection.
static void handle_job(int conn_fd) {
  std::string msg;
  if (!pnm_recv_frame(conn_fd, &msg)) {
    fprintf(stderr, "[pnm_unit] recv job failed\n");
    return;
  }

  // Parse compound message: db_name | job_id_str | compaction_service_input
  std::string db_name, job_id_str, input;
  size_t pos = 0;
  const char* buf = msg.data();
  size_t len = msg.size();
  if (!parse_field(buf, len, &pos, &db_name) ||
      !parse_field(buf, len, &pos, &job_id_str) ||
      !parse_field(buf, len, &pos, &input)) {
    fprintf(stderr, "[pnm_unit] malformed job message\n");
    std::string resp(1, '\x01');
    pnm_send_frame(conn_fd, resp);
    return;
  }

  fprintf(stderr, "[pnm_unit] job=%s db=%s input_bytes=%zu\n",
          job_id_str.c_str(), db_name.c_str(), input.size());

  // Serialize: one compaction at a time on the shared secondary DB.
  std::lock_guard<std::mutex> lk(g_compact_mutex);

  // Open secondary DB once (no-op on subsequent calls).
  Status s = ensure_secondary(db_name);
  if (!s.ok()) {
    fprintf(stderr, "[pnm_unit] secondary DB open failed: %s — "
                    "falling back to OpenAndCompact\n", s.ToString().c_str());
    // Fallback: use the original heavyweight path.
    ROCKSDB_NAMESPACE::CompactionServiceOptionsOverride override_opts;
    std::string output;
    std::string output_dir = std::string(kSecondaryPath) + "/" + job_id_str;
    mkdir(output_dir.c_str(), 0755);
    s = DB::OpenAndCompact(db_name, output_dir, input, &output, override_opts);
    std::string resp;
    resp.push_back(s.ok() ? '\x00' : '\x01');
    if (s.ok()) resp.append(output);
    if (!pnm_send_frame(conn_fd, resp))
      fprintf(stderr, "[pnm_unit] send response failed (fd=%d)\n", conn_fd);
    return;
  }

  // Fast incremental MANIFEST sync — only processes new entries since last call.
  g_secondary_db->TryCatchUpWithPrimary();

  // Parse the compaction job input.
  CompactionServiceInput csi;
  s = CompactionServiceInput::Read(input, &csi);
  if (!s.ok()) {
    fprintf(stderr, "[pnm_unit] failed to parse CompactionServiceInput: %s\n",
            s.ToString().c_str());
    std::string resp(1, '\x01');
    if (!pnm_send_frame(conn_fd, resp))
      fprintf(stderr, "[pnm_unit] send response failed (fd=%d)\n", conn_fd);
    return;
  }

  // Run compaction on the persistent secondary — no DB re-open!
  // Output SST files go to kSecondaryPath; result.output_path = kSecondaryPath.
  OpenAndCompactOptions oc_opts;
  CompactionServiceResult result;
  s = g_secondary_db->CompactWithoutInstallation(
      oc_opts, g_secondary_db->DefaultColumnFamily(), csi, &result);

  if (!s.ok() || !result.status.ok()) {
    const std::string& err = s.ok() ? result.status.ToString() : s.ToString();
    fprintf(stderr, "[pnm_unit] CompactWithoutInstallation failed job=%s: %s\n",
            job_id_str.c_str(), err.c_str());
    std::string resp(1, '\x01');
    if (!pnm_send_frame(conn_fd, resp))
      fprintf(stderr, "[pnm_unit] send failure response failed (fd=%d)\n",
              conn_fd);
    return;
  }

  // Serialize the result and send it back.
  std::string out_str;
  s = result.Write(&out_str);
  if (!s.ok()) {
    fprintf(stderr, "[pnm_unit] CompactionServiceResult::Write failed: %s\n",
            s.ToString().c_str());
    std::string resp(1, '\x01');
    pnm_send_frame(conn_fd, resp);
    return;
  }

  fprintf(stderr, "[pnm_unit] job=%s done output_bytes=%zu output_path=%s\n",
          job_id_str.c_str(), out_str.size(), result.output_path.c_str());

  // Record counts and sizes, so the worker's deduplication can be compared
  // with the baseline's compaction statistics.
  const auto& job_stats = result.stats;
  fprintf(stderr, "[pnm_unit] job=%s records in=%llu out=%llu replaced=%llu "
                  "bytes in=%llu out=%llu\n",
          job_id_str.c_str(),
          static_cast<unsigned long long>(job_stats.num_input_records),
          static_cast<unsigned long long>(job_stats.num_output_records),
          static_cast<unsigned long long>(job_stats.num_records_replaced),
          static_cast<unsigned long long>(result.bytes_read),
          static_cast<unsigned long long>(result.bytes_written));

  // Poll MMIO STATUS_DONE on core 1 before responding to the main process.
  // This keeps core 0 free (rocksdb_pnm blocks in recv()) while we absorb the
  // PNM timing model latency here.  Schedule() wrote CMD_SUBMIT; we wait
  // for the gem5 PNMCompactor event to fire before unblocking Wait().
  if (g_mmio_base) {
    while (true) {
      uint32_t st = __atomic_load_n(
          reinterpret_cast<const volatile uint32_t*>(g_mmio_base + kPNMOffStatus),
          __ATOMIC_SEQ_CST);
      if (st & kPNMStatusDone || st & kPNMStatusErr) break;
      sched_yield();
    }
  }

  std::string resp;
  resp.push_back('\x00');  // status=0 (success)
  resp.append(out_str);
  if (!pnm_send_frame(conn_fd, resp))
    fprintf(stderr, "[pnm_unit] send success response failed (fd=%d)\n",
            conn_fd);
}

int main() {
  // Pin this process to core 1, the PNM core; rocksdb_pnm keeps core 0.
  cpu_set_t cpuset;
  CPU_ZERO(&cpuset);
  CPU_SET(1, &cpuset);
  if (sched_setaffinity(0, sizeof(cpuset), &cpuset) != 0) {
    fprintf(stderr, "[pnm_unit] sched_setaffinity core 1 failed (%s) "
                    "— continuing without pinning\n", strerror(errno));
  } else {
    fprintf(stderr, "[pnm_unit] pinned to core 1\n");
  }

  // Map the PNM MMIO region so handle_job() can poll STATUS on core 1.
  int mmio_fd = open("/dev/pnm", O_RDWR | O_SYNC);
  if (mmio_fd >= 0) {
    void* p = mmap(nullptr, kPNMSize, PROT_READ | PROT_WRITE, MAP_SHARED,
                   mmio_fd, 0);
    if (p != MAP_FAILED)
      g_mmio_base = static_cast<volatile uint8_t*>(p);
    else
      fprintf(stderr, "[pnm_unit] mmap /dev/pnm failed (%s) — MMIO poll disabled\n",
              strerror(errno));
    close(mmio_fd);  // mmap keeps the mapping alive after fd close
  } else {
    fprintf(stderr, "[pnm_unit] /dev/pnm not available (%s) — MMIO poll disabled\n",
            strerror(errno));
  }

  // Remove stale socket and secondary path from previous runs.
  unlink(kPNMSockPath);

  int srv = socket(AF_UNIX, SOCK_STREAM, 0);
  if (srv < 0) {
    fprintf(stderr, "[pnm_unit] socket() failed: %s\n", strerror(errno));
    return 1;
  }

  struct sockaddr_un addr{};
  addr.sun_family = AF_UNIX;
  strncpy(addr.sun_path, kPNMSockPath, sizeof(addr.sun_path) - 1);

  if (bind(srv, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) != 0) {
    fprintf(stderr, "[pnm_unit] bind failed: %s\n", strerror(errno));
    return 1;
  }

  if (listen(srv, 8) != 0) {
    fprintf(stderr, "[pnm_unit] listen failed: %s\n", strerror(errno));
    return 1;
  }

  fprintf(stderr, "[pnm_unit] waiting for jobs on %s\n", kPNMSockPath);

  while (true) {
    int conn = accept(srv, nullptr, nullptr);
    if (conn < 0) {
      if (errno == EINTR) continue;
      fprintf(stderr, "[pnm_unit] accept error: %s\n", strerror(errno));
      break;
    }
    // Thread per job: accept() never blocks while a compaction runs.
    // The g_compact_mutex inside handle_job serialises actual compaction.
    std::thread([conn]{ handle_job(conn); close(conn); }).detach();
  }

  close(srv);
  unlink(kPNMSockPath);
  return 0;
}
