import os

# The only place for the version. Write it exactly like the release tag without "v":
# 0.2.0 for v0.2.0, 0.2.0-rc.1 for v0.2.0-rc.1 (pip shows that as 0.2.0rc1, which is the same).
__version__ = "0.3.1"

# Published image; the wrapper passes AGENTBOX_IMAGE (e.g. a local dev build).
IMAGE_REPO = "ghcr.io/redrem95/agentbox"


def default_image() -> str:
    return os.environ.get("AGENTBOX_IMAGE") or f"{IMAGE_REPO}:{__version__}"
