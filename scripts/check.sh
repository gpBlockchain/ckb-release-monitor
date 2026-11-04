#!/usr/bin/env bash
# Usage: ./scripts/check.sh   (env: UPSTREAM=nervosnetwork/ckb MONITOR_DAYS=183)
# Exit 0 = no tampering; 1 = diff/mismatch (see out/alert.txt). New baselines -> out/new.txt
set -euo pipefail
UPSTREAM="${UPSTREAM:-nervosnetwork/ckb}"; MONITOR_DAYS="${MONITOR_DAYS:-183}"
cutoff=$(date -u -d "-$MONITOR_DAYS days" +%Y-%m-%dT%H:%M:%SZ)
cd "$(dirname "$0")/.."
mkdir -p baseline out; : > out/alert.txt; : > out/new.txt
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
releases=$(gh api --paginate "repos/$UPSTREAM/releases?per_page=50" -q ".[] | select((.draft|not) and .published_at >= \"$cutoff\") | .tag_name")
echo "Checking releases published since $cutoff:" $releases
for tag in $releases; do
  rel=$(gh api "repos/$UPSTREAM/releases/tags/$tag")
  ref=$(gh api "repos/$UPSTREAM/git/ref/tags/$tag")
  sha=$(jq -r .object.sha <<<"$ref"); typ=$(jq -r .object.type <<<"$ref")
  while [ "$typ" = tag ]; do o=$(gh api "repos/$UPSTREAM/git/tags/$sha"); sha=$(jq -r .object.sha <<<"$o"); typ=$(jq -r .object.type <<<"$o"); done
  now="$tmp/$tag.txt"
  {
    echo "tag $tag"; echo "commit $sha"
    jq -r '"published_at \(.published_at)\nauthor \(.author.login)\nprerelease \(.prerelease)"' <<<"$rel"
  } > "$now"
  d="$tmp/dl"; rm -rf "$d"; mkdir -p "$d"
  for name in $(jq -r '.assets[].name' <<<"$rel" | sort); do
    gh release download "$tag" -R "$UPSTREAM" -p "$name" -D "$d" --clobber
    size=$(stat -c %s "$d/$name"); h=$(sha256sum "$d/$name" | cut -d' ' -f1)
    echo "asset $name $size $h" >> "$now"
    # verify any upstream checksum file shipped in the release
    case "$name" in *checksum*|*CHECKSUM*|*SHA256SUM*|*.sha256) : ;; *) [ "$name" = "${name%.sha256}" ] && rm -f "$d/$name"; continue;; esac
  done
  for cf in "$d"/*; do
    [ -e "$cf" ] || continue
    while read -r ch cn; do
      cn="${cn#\*}"; cn="${cn##*/}"; [[ "$ch" =~ ^[0-9a-f]{64}$ ]] || continue
      got=$(awk -v n="$cn" '$1=="asset"&&$2==n{print $4}' "$now")
      if [ -n "$got" ] && [ "$got" != "$ch" ]; then echo "CHECKSUM-MISMATCH $tag $cn upstream=$ch actual=$got" >> out/alert.txt; fi
    done < "$cf"
  done
  f="baseline/$tag.txt"
  if [ ! -f "$f" ]; then cp "$now" "$f"; echo "$tag" >> out/new.txt; echo "NEW $tag"
  elif ! diff -u "$f" "$now" > "out/$tag.diff"; then echo "CHANGED $tag" >> out/alert.txt; cat "out/$tag.diff"
  else rm -f "out/$tag.diff"; echo "OK $tag"; fi
done
if [ -s out/alert.txt ]; then echo "ALERT:"; cat out/alert.txt; exit 1; fi
echo "All checked releases match baseline."
