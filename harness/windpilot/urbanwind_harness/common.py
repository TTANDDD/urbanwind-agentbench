from __future__ import annotations

import hashlib
import json
import math
import os
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: Path):
    if path.stat().st_size > 512*1024*1024:
        raise ValueError("json_size_limit")
    def nonfinite(value):
        raise ValueError("nonfinite_json_number:"+value)
    def finite_float(value):
        number=float(value)
        if not math.isfinite(number):
            raise ValueError('nonfinite_json_number:'+value)
        return number
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=nonfinite, parse_float=finite_float)


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        temporary = handle.name
    os.replace(temporary, path)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def confined(root: Path, raw: str) -> Path:
    path = (root / raw).resolve()
    if path != root.resolve() and root.resolve() not in path.parents:
        raise ValueError("path_escape")
    return path


def extract(archive: Path, destination: Path, max_bytes: int = 2**30) -> None:
    """Only regular files/directories, no links, devices or overwrite."""
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        if sum(item.size for item in members) > max_bytes:
            raise ValueError("archive_size_limit")
        seen = set()
        for item in members:
            path = confined(destination, item.name)
            if item.name.startswith("/") or "\\" in item.name:
                raise ValueError("unsafe_archive_name")
            if not (item.isfile() or item.isdir()):
                raise ValueError("archive_links_or_special_file")
            if path in seen and item.isfile():
                raise ValueError("duplicate_archive_file")
            seen.add(path)
        for item in members:
            path = confined(destination, item.name)
            if item.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with handle.extractfile(item) as source, path.open("xb") as target:
                    while block := source.read(1024 * 1024):
                        target.write(block)
                path.chmod(0o755 if item.mode & 0o111 else 0o644)
