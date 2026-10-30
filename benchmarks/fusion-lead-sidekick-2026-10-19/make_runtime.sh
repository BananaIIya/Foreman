#!/bin/bash
# Rebuilds runtime/codex-linux-0.163.0-alpha.10.2.tgz (codex + rg for the in-container exec-server; ~115 MB, not committed).
# Source: the official npm package @openai/codex@0.163.0-alpha.10.2-linux-x64; its sha512 must match the registry's.
set -euo pipefail
cd "$(dirname "$0")"
VERSION=0.163.0-alpha.10.2
PKG="openai-codex-$VERSION-linux-x64.tgz"
mkdir -p runtime/npm-codex-linux runtime/bin
(cd runtime/npm-codex-linux && npm pack "@openai/codex@$VERSION-linux-x64" >/dev/null)
want=$(npm view "@openai/codex@$VERSION-linux-x64" dist.integrity)
got="sha512-$(shasum -a 512 "runtime/npm-codex-linux/$PKG" | awk '{print $1}' | xxd -r -p | base64)"
[ "$want" = "$got" ] || { echo "integrity mismatch: $got != $want" >&2; exit 1; }
stage=$(mktemp -d)
tar -xzf "runtime/npm-codex-linux/$PKG" -C "$stage" \
  package/vendor/x86_64-unknown-linux-musl/bin/codex package/vendor/x86_64-unknown-linux-musl/codex-path/rg
mv "$stage/package/vendor/x86_64-unknown-linux-musl/bin/codex" "$stage/package/vendor/x86_64-unknown-linux-musl/codex-path/rg" runtime/bin/
rm -rf "$stage"
tar -czf "runtime/codex-linux-$VERSION.tgz" -C runtime/bin codex rg
echo "built runtime/codex-linux-$VERSION.tgz"
