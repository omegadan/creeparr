"""Filesystem helpers."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def free_space_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def atomic_replace(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dest)


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".patrearr-write-test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def try_hardlink(target: Path, source: Path) -> bool:
    """Replace `target` with a hardlink to `source` (same content). Same filesystem only."""
    try:
        if not source.exists() or not target.exists():
            return False
        ts, ss = target.stat(), source.stat()
        if ts.st_dev != ss.st_dev:
            return False
        if ts.st_ino == ss.st_ino:
            return True  # already the same inode
        tmp = target.with_name(target.name + ".dedupe-tmp")
        os.link(source, tmp)
        os.replace(tmp, target)
        return True
    except OSError:
        return False
