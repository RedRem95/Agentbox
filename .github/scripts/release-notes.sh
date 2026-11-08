#!/usr/bin/env bash
# Builds the release notes on stdout:
#   1. the matching section from CHANGELOG.md (hand-written, what users need to know)
#   2. install instructions for exactly this release
#   3. GitHub's generated list of merged PRs since the previous tag (needs GH_TOKEN)
#
# usage: release-notes.sh <version> <tag> <owner/repo> <image> [changelog]
set -euo pipefail

version=$1 tag=$2 repo=$3 image=$4 changelog=${5:-CHANGELOG.md}

section=$(awk -v head="## [$version]" '
  index($0, head) == 1                    { on = 1; next }
  on && (/^## \[/ || /^\[[^]]+\]: /)      { exit }
  on                                      { print }
' "$changelog" | sed '/./,$!d')
section="${section%"${section##*[![:space:]]}"}"
[[ -n "$section" ]] || section="Release $version"

generated=""
if [[ -n "${GH_TOKEN:-}" ]]; then
  prev=$(git describe --tags --abbrev=0 "$tag^" 2>/dev/null || true)
  args=(-f tag_name="$tag")
  [[ -n "$prev" ]] && args+=(-f previous_tag_name="$prev")
  generated=$(gh api "repos/$repo/releases/generate-notes" "${args[@]}" --jq .body 2>/dev/null || true)
fi

cat <<NOTES
$section

## Install

\`\`\`bash
curl -fsSLo agentbox https://github.com/$repo/releases/download/$tag/agentbox
chmod +x agentbox && ./agentbox init
\`\`\`

With uv instead: \`uvx --from "git+https://github.com/$repo@$tag#subdirectory=cli" agentbox init\`

Image: \`$image:$version\`
NOTES

if [[ -n "$generated" ]]; then
  printf '\n%s\n' "$generated"
fi
