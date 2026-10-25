"""Archive quota counts keep their fail-closed boundary while reducing stats."""

import os
from contextlib import contextmanager

import pytest

from venom_module_rider import sample_usage
from venom_module_rider.errors import RiderError
from venom_module_rider.sample_usage import tree_bytes


def test_tree_bytes_counts_regular_files_and_optional_inode_dedup(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    original = tmp_path / "raw.bin"
    original.write_bytes(b"12345")
    os.link(original, nested / "shared.bin")
    (nested / "manifest.json").write_bytes(b"123")
    os.mkfifo(nested / "unrelated-pipe")
    assert tree_bytes(tmp_path) == 13
    assert tree_bytes(tmp_path, deduplicate=True) == 8
    assert tree_bytes(tmp_path / "missing") == 0
    assert tree_bytes(original) == 0


@pytest.mark.parametrize("kind", ["root", "directory", "file", "dangling"])
def test_tree_bytes_rejects_every_symlink_kind(tmp_path, kind):
    target = tmp_path / "target"
    target.mkdir()
    (target / "raw.bin").write_bytes(b"123")
    root = tmp_path / "archive"
    if kind == "root":
        root.symlink_to(target, target_is_directory=True)
    else:
        root.mkdir()
        destination = (
            target
            if kind == "directory"
            else target / "raw.bin"
            if kind == "file"
            else tmp_path / "missing"
        )
        (root / "link").symlink_to(destination)
    with pytest.raises(RiderError, match="samples_root_invalid"):
        tree_bytes(root)


def test_unlinked_atomic_temporary_file_is_skipped(tmp_path, monkeypatch):
    (tmp_path / "keep").write_bytes(b"123")
    (tmp_path / "temporary").write_bytes(b"12345")
    scan = os.scandir

    @contextmanager
    def unlink_before_stat(descriptor):
        with scan(descriptor) as entries:

            def remaining():
                for entry in entries:
                    if entry.name == "temporary":
                        os.unlink(entry.name, dir_fd=descriptor)
                    yield entry

            yield remaining()

    monkeypatch.setattr(sample_usage.os, "scandir", unlink_before_stat)
    assert tree_bytes(tmp_path) == 3


def test_directory_swapped_to_symlink_before_open_fails_closed(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    child = archive / "child"
    child.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (external / "raw.bin").write_bytes(b"not-an-archive")
    open_directory = os.open

    def replaced(path, flags):
        if os.fspath(path) == os.fspath(child):
            child.rmdir()
            child.symlink_to(external, target_is_directory=True)
        return open_directory(path, flags)

    monkeypatch.setattr(sample_usage.os, "open", replaced)
    with pytest.raises(RiderError, match="samples_root_invalid"):
        tree_bytes(archive)


def test_io_error_cannot_silently_undercount_quota(tmp_path, monkeypatch):
    def denied(*_args):
        raise PermissionError("fixture")

    monkeypatch.setattr(sample_usage.os, "open", denied)
    with pytest.raises(PermissionError):
        tree_bytes(tmp_path)
