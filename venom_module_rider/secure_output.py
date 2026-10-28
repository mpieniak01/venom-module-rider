"""Descriptor-relative output for offline evidence tools on supported POSIX hosts."""

import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def output_directory(path: Path, *, create: bool = False):
    """Pin each directory without following symlinks; ancestors remain trusted."""
    path = Path(path).absolute()
    if (
        ".." in path.parts
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
    ):
        raise ValueError("secure_output_directory")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
        verify_directory(path, descriptor)
    finally:
        os.close(descriptor)


def exclusive_file(directory: int, name: str, *, binary: bool = True):
    """Create one regular output without replacement relative to a pinned directory."""
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError("secure_output_name")
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory,
    )
    try:
        if binary:
            return os.fdopen(descriptor, "wb")
        return os.fdopen(descriptor, "w", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise


def verify_directory(path: Path, descriptor: int) -> None:
    """Reject replacement of the named output after descriptor-relative writes."""
    pinned = os.fstat(descriptor)
    try:
        named = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError("secure_output_directory_changed") from exc
    if (named.st_dev, named.st_ino) != (pinned.st_dev, pinned.st_ino):
        raise ValueError("secure_output_directory_changed")


@contextmanager
def new_directory(parent: int, name: str):
    """Create and pin a fresh direct child, closing it even if outer checks fail."""
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError("secure_output_name")
    os.mkdir(name, mode=0o700, dir_fd=parent)
    descriptor = os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
    )
    try:
        yield descriptor
    finally:
        os.close(descriptor)
