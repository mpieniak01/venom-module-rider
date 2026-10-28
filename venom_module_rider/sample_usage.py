"""Read-only archive accounting without repeated pathlib metadata walks."""

import errno
import os
import stat

from .errors import RiderError


def tree_bytes(root, *, deduplicate=False):
    """Count regular files; never follow an archive root or nested symlink."""
    if not _root_directory(root):
        return 0
    pending = [os.fspath(root)]
    seen: set[tuple[int, int]] = set()
    total = 0
    while pending:
        directory = pending.pop()
        total += _directory_bytes(directory, pending, seen, deduplicate)
    return total


def _root_directory(root):
    """Classify the archive root without following a root symlink."""
    try:
        root_info = os.lstat(root)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(root_info.st_mode):
        raise RiderError("samples_root_invalid", 400)
    return stat.S_ISDIR(root_info.st_mode)


def _directory_bytes(directory, pending, seen, deduplicate):
    """Account one NOFOLLOW directory and close its descriptor on every path."""
    try:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return 0
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise RiderError("samples_root_invalid", 400) from exc
        raise
    try:
        with os.scandir(descriptor) as entries:
            return sum(
                _entry_bytes(entry, directory, pending, seen, deduplicate)
                for entry in entries
            )
    finally:
        os.close(descriptor)


def _entry_bytes(entry, directory, pending, seen, deduplicate):
    """Classify one entry and optionally deduplicate regular-file inodes."""
    try:
        info = entry.stat(follow_symlinks=False)
    except FileNotFoundError:
        return 0
    if stat.S_ISLNK(info.st_mode):
        raise RiderError("samples_root_invalid", 400)
    if stat.S_ISDIR(info.st_mode):
        pending.append(os.path.join(directory, entry.name))
    elif stat.S_ISREG(info.st_mode):
        if deduplicate:
            inode = info.st_dev, info.st_ino
            if inode in seen:
                return 0
            seen.add(inode)
        return info.st_size
    return 0
