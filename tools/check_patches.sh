#!/usr/bin/env bash
# Check that patches/ accounts exactly for every change to the two upstream trees' changed files.
#
#     tools/check_patches.sh
#
# For each file a patch touches, fetch that file at the upstream version this repository vendors
# (gem5 v25.1.0.1, RocksDB e492562), apply the patch to it, and compare the result byte for byte
# with the copy under gem5/ or rocksdb/. Needs bash, curl and git; downloads only those files.

set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

GEM5_URL="https://raw.githubusercontent.com/gem5/gem5/v25.1.0.1"
ROCKSDB_URL="https://raw.githubusercontent.com/facebook/rocksdb/e492562651b295089bf15cb8c4237f1da8296456"

failed=0

check() {  # <vendored directory> <upstream raw-file URL> <patch>
    local tree=$1 url=$2 patch=$3
    local dir="$work/$tree"
    mkdir -p "$dir"
    local files
    files=$(sed -n 's|^diff --git a/\(.*\) b/.*|\1|p' "$patch")
    for f in $files; do
        mkdir -p "$dir/$(dirname "$f")"
        curl -fsSL "$url/$f" -o "$dir/$f"
    done
    (cd "$dir" && git apply "$patch")
    for f in $files; do
        if cmp -s "$dir/$f" "$repo/$tree/$f"; then
            echo "PASS  $tree/$f"
        else
            echo "FAIL  $tree/$f differs from upstream + $(basename "$patch")"
            failed=1
        fi
    done
}

check gem5    "$GEM5_URL"    "$repo/patches/gem5.patch"
check rocksdb "$ROCKSDB_URL" "$repo/patches/rocksdb.patch"

if [ "$failed" -eq 0 ]; then
    echo "patches/ reproduces every changed upstream file exactly"
fi
exit "$failed"
