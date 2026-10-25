"""Read-only archive accounting without repeated pathlib metadata walks."""

import errno
import os
import stat

from .errors import RiderError


def tree_bytes(root, *, deduplicate=False):
    """Count regular files; never follow an archive root or nested symlink."""
    try:
        root_info = os.lstat(root)
    except FileNotFoundError:
        return 0
    if stat.S_ISLNK(root_info.st_mode):
        raise RiderError("samples_root_invalid", 400)
    if not stat.S_ISDIR(root_info.st_mode):
        return 0
    pending = [os.fspath(root)]
    seen = set()
    total = 0
    while pending:
        directory = pending.pop()
        try:
            # NOFOLLOW also closes the directory-to-symlink replacement race
            # between discovering a child and opening its scandir iterator.
            descriptor = os.open(
                directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
        except FileNotFoundError:
            continue
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise RiderError("samples_root_invalid", 400) from exc
            raise
        try:
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        # An atomic writer may unlink its private temp name.
                        continue
                    if stat.S_ISLNK(info.st_mode):
                        raise RiderError("samples_root_invalid", 400)
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(os.path.join(directory, entry.name))
                    elif stat.S_ISREG(info.st_mode):
                        if deduplicate:
                            inode = info.st_dev, info.st_ino
                            if inode in seen:
                                continue
                            seen.add(inode)
                        total += info.st_size
        finally:
            os.close(descriptor)
    return total
