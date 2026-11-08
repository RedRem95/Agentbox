#!/usr/bin/env bash
# Builds the release notes on stdout:
#   0. existing release text (if the release was written in the web UI) – kept as is, on top
#   1. the matching section from CHANGELOG.md (hand-written, what users need to know)
#   2. install instructions for exactly this release
#   3. GitHub's generated list of merged PRs since the previous tag (needs GH_TOKEN)
# Parts that the existing text already contains are not added again, so re-runs are harmless.
#
# usage: release-notes.sh <version> <tag> <owner/repo> <image> [changelog]
# env:   EXISTING_NOTES  path to a file with the current release body (optional)
set -euo pipefail

version=$1 tag=$2 repo=$3 image=$4 changelog=${5:-CHANGELOG.md}
PLACEHOLDER="<!-- agentbox:placeholder -->"

trim() {  # strip leading and trailing blank lines
  local s
  s=$(printf '%s\n' "$1" | sed '/./,$!d')
  printf '%s' "${s%"${s##*[![:space:]]}"}"
}

existing=""
if [[ -n "${EXISTING_NOTES:-}" && -f "$EXISTING_NOTES" ]]; then
  existing=$(tr -d '\r' < "$EXISTING_NOTES")
  [[ "$existing" == *"$PLACEHOLDER"* ]] && existing=""
  existing=$(trim "$existing")
fi

section=$(awk -v head="## [$version]" '
  index($0, head) == 1                    { on = 1; next }
  on && (/^## \[/ || /^\[[^]]+\]: /)      { exit }
  on                                      { print }
' "$changelog")
section=$(trim "$section")

generated=""
if [[ -n "${GH_TOKEN:-}" ]]; then
  prev=$(git describe --tags --abbrev=0 "$tag^" 2>/dev/null || true)
  args=(-f tag_name="$tag")
  [[ -n "$prev" ]] && args+=(-f previous_tag_name="$prev")
  generated=$(gh api "repos/$repo/releases/generate-notes" "${args[@]}" --jq .body 2>/dev/null || true)
fi

install=$(cat <<NOTES
## Install

\`\`\`bash
curl -fsSLo agentbox https://github.com/$repo/releases/download/$tag/agentbox
chmod +x agentbox && ./agentbox init
\`\`\`

With uv instead: \`uvx --from "git+https://github.com/$repo@$tag#subdirectory=cli" agentbox init\`

Image: \`$image:$version\`
NOTES
)

parts=()
[[ -n "$existing" ]] && parts+=("$existing")
# changelog: skip if its first line is already in the text
if [[ -n "$section" ]]; then
  first=$(printf '%s\n' "$section" | head -n1)
  [[ "$existing" == *"$first"* ]] || parts+=("$section")
fi
[[ ${#parts[@]} -gt 0 ]] || parts+=("Release $version")
[[ "$existing" == *"## Install"* ]] || parts+=("$install")
if [[ -n "$generated" && "$existing" != *"What's Changed"* && "$existing" != *"Full Changelog"* ]]; then
  parts+=("$(trim "$generated")")
fi

out=""
for p in "${parts[@]}"; do
  out+="${out:+$'\n\n'}$p"
done
printf '%s\n' "$out"
