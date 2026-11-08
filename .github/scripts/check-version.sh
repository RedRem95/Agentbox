#!/usr/bin/env bash
# Checks that a release tag matches the package version and prints the version (tag without "v").
#
#   check-version.sh v0.2.0-rc.1
#
# - the tag must be semver (vX.Y.Z or vX.Y.Z-pre), otherwise the image tags can't be derived
# - the version pip/uv see (resolved from cli/pyproject.toml) must be the same version (PEP 440,
#   so 0.2.0rc1 counts as 0.2.0-rc.1)
# - __version__ must be written exactly like the tag, because the image name is built from it
set -euo pipefail

tag=${1:?usage: check-version.sh <tag>}
version=${tag#v}
cli_dir=$(cd "${CLI_DIR:-cli}" && pwd)   # absolute: pip reads a bare "cli" as a PyPI package name
fail() { echo "::error::$*" >&2; exit 1; }

[[ "$tag" == v* ]] || fail "Tag $tag must start with v (e.g. v0.2.0)"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?$ ]] \
  || fail "Tag $tag is not semver (vX.Y.Z or vX.Y.Z-rc.1)"

venv=$(mktemp -d)
python3 -m venv "$venv"
"$venv/bin/pip" install -q --disable-pip-version-check "$cli_dir" packaging >&2

read -r pkg src < <("$venv/bin/python" -c '
import importlib.metadata, agentbox_cli
print(importlib.metadata.version("agentbox"), agentbox_cli.__version__)')
same=$("$venv/bin/python" -c '
import sys
from packaging.version import InvalidVersion, Version
try:
    print(Version(sys.argv[1]) == Version(sys.argv[2]))
except InvalidVersion:
    print(False)' "$version" "$pkg")
rm -rf "$venv"

[[ "$same" == True ]] || fail "Release $tag does not match the version in $cli_dir/pyproject.toml ($pkg)"
[[ "$src" == "$version" ]] || fail "Write __version__ exactly like the tag: \"$version\" (is \"$src\")"
echo "$version"
