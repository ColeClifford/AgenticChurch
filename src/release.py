"""Which commit of the church's source is running, so anyone can check the site against the repository.

The commit is baked into the image when it's built (the `GIT_SHA` build argument, see the
Dockerfile). A build without one, such as a local run, reports `null`.
"""

import os
import re

SOURCE = "https://github.com/ColeClifford/AgenticChurch"


def info() -> dict:
    sha = os.environ.get("GIT_SHA", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        sha = ""
    return {
        "commit": sha or None,
        "commit_url": f"{SOURCE}/commit/{sha}" if sha else None,
        "source": SOURCE,
    }
