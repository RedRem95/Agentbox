import os

__version__ = "0.1.0"

# Published image; the wrapper passes AGENTBOX_IMAGE (e.g. a local dev build).
IMAGE_REPO = "ghcr.io/redrem95/agentbox"


def default_image() -> str:
    return os.environ.get("AGENTBOX_IMAGE") or f"{IMAGE_REPO}:{__version__}"
