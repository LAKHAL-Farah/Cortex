#!/usr/bin/env bash
# Pin every `FROM image:tag` in the repo's Dockerfiles to its current digest
# (FROM image:tag@sha256:...). Dependabot's docker ecosystem then bumps them.
# Requires: docker with buildx, network access to the registries. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."

mapfile -t files < <(git ls-files '*Dockerfile')
for f in "${files[@]}"; do
  # Skip *-sim Dockerfiles: sandbox fixtures, never deployed.
  case "$f" in *-sim/*) continue;; esac
  while read -r ref; do
    case "$ref" in *@sha256:*|scratch) continue;; esac
    # Skip references to earlier build stages.
    if grep -qiE "^FROM .* AS ${ref}[[:space:]]*\$" "$f"; then continue; fi
    printf 'Inspecting image [%s] in [%s]\n' "$ref" "$f"
    digest=$(docker buildx imagetools inspect "$ref" --format '{{.Manifest.Digest}}')
    echo "$f: $ref -> $digest"
    sed -i -E "s#^(FROM )${ref}([[:space:]]|\$)#\1${ref}@${digest}\2#I" "$f"
  done < <(awk 'toupper($1)=="FROM"{sub(/\r$/,"",$2); print $2}' "$f" | sort -u)
done
