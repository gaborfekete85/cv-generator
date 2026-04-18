"""Parse a profile file into a dict.

Two formats are supported:

  * Markdown with YAML front matter (``.md``) — the original format used by
    the bundled demo profiles.
  * Plain JSON (``.json``) — used for user-created profiles saved via the
    /api/my-profile endpoint. JSON is easier for the frontend form to
    serialise round-trip than YAML.

The output shape is identical regardless of the file format, so the CV
template and matcher don't care which one produced it.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def load_profile(path: str | Path) -> dict:
    path = Path(path)
    text = path.read_text(encoding="utf-8")

    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("JSON profile must be a top-level object")
        # JSON profiles don't have the long-form prose body — set empty so
        # downstream template code can reference `profile.body` safely.
        data.setdefault("body", "")
        return data

    # Default: Markdown + YAML front matter
    front, body = _split_front_matter(text)
    data = yaml.safe_load(front) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: YAML front matter must be a mapping")
    data["body"] = body.strip()
    return data


def _split_front_matter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("Markdown profile must begin with '---' (YAML front matter)")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration as e:
        raise ValueError("Markdown profile has no closing '---' for front matter") from e
    front = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :])
    return front, body
