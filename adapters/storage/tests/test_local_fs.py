import pytest

from adapters.storage.local_fs import LocalFsBackend
from caps.blobstore import BlobStore

KEY = "sha256/ab/cd/" + "ab" * 32


def _backend(tmp_path):
    return LocalFsBackend(root=tmp_path / "blobs")


# ── 构造 ───────────────────────────────────────────────────────────────


def test_constructor_creates_root_directory(tmp_path):
    root = tmp_path / "blobs"
    assert not root.exists()
    LocalFsBackend(root=root)
    assert root.is_dir()


# ── open_write / finalize ────────────────────────────────────────────────


def test_open_write_returns_handle_and_pending_id(tmp_path):
    backend = _backend(tmp_path)
    handle, pending = backend.open_write()
    assert isinstance(pending, str)
    assert pending
    handle.close()


def test_finalize_moves_content_to_key_path(tmp_path):
    backend = _backend(tmp_path)
    handle, pending = backend.open_write()
    handle.write(b"hello world")
    handle.close()

    result = backend.finalize(pending, KEY)
    assert result is True
    assert backend.exists(KEY)
    with backend.open_read(KEY) as f:
        assert f.read() == b"hello world"


def test_finalize_creates_nested_directories(tmp_path):
    backend = _backend(tmp_path)
    handle, pending = backend.open_write()
    handle.write(b"data")
    handle.close()
    backend.finalize(pending, KEY)
    assert (tmp_path / "blobs" / "sha256" / "ab" / "cd").is_dir()


def test_finalize_dedup_when_key_already_exists(tmp_path):
    backend = _backend(tmp_path)

    h1, p1 = backend.open_write()
    h1.write(b"first write")
    h1.close()
    assert backend.finalize(p1, KEY) is True

    h2, p2 = backend.open_write()
    h2.write(b"second write with different bytes")
    h2.close()
    second_result = backend.finalize(p2, KEY)

    assert second_result is False
    # 已经落位的那份内容不会被第二次写入覆盖
    with backend.open_read(KEY) as f:
        assert f.read() == b"first write"


def test_finalize_dedup_removes_leftover_temp_file(tmp_path):
    backend = _backend(tmp_path)
    h1, p1 = backend.open_write()
    h1.write(b"x")
    h1.close()
    backend.finalize(p1, KEY)

    h2, p2 = backend.open_write()
    h2.write(b"y")
    h2.close()
    backend.finalize(p2, KEY)

    assert not (tmp_path / "blobs" / ".tmp" / p2).exists()


# ── abort ────────────────────────────────────────────────────────────────


def test_abort_removes_temp_file(tmp_path):
    backend = _backend(tmp_path)
    handle, pending = backend.open_write()
    handle.write(b"abandoned")
    handle.close()

    backend.abort(pending)
    assert not (tmp_path / "blobs" / ".tmp" / pending).exists()


def test_abort_is_idempotent_when_temp_missing(tmp_path):
    backend = _backend(tmp_path)
    backend.abort("never-existed")  # 不应报错


# ── open_read / exists / size ─────────────────────────────────────────────


def test_open_read_missing_key_raises_keyerror(tmp_path):
    backend = _backend(tmp_path)
    with pytest.raises(KeyError):
        backend.open_read(KEY)


def test_exists_false_when_missing(tmp_path):
    backend = _backend(tmp_path)
    assert backend.exists(KEY) is False


def test_size_none_when_missing(tmp_path):
    backend = _backend(tmp_path)
    assert backend.size(KEY) is None


def test_size_returns_byte_count(tmp_path):
    backend = _backend(tmp_path)
    handle, pending = backend.open_write()
    handle.write(b"12345")
    handle.close()
    backend.finalize(pending, KEY)
    assert backend.size(KEY) == 5


# ── delete ────────────────────────────────────────────────────────────────


def test_delete_removes_file_and_returns_true(tmp_path):
    backend = _backend(tmp_path)
    handle, pending = backend.open_write()
    handle.write(b"to be deleted")
    handle.close()
    backend.finalize(pending, KEY)

    assert backend.delete(KEY) is True
    assert backend.exists(KEY) is False


def test_delete_missing_returns_false(tmp_path):
    backend = _backend(tmp_path)
    assert backend.delete(KEY) is False


# ── 和真实 BlobStore 的集成（确认协议对得上，不是只对单测友好） ───────────


def test_integrates_with_real_blobstore(tmp_path):
    store = BlobStore(_backend(tmp_path))

    first = store.put(b"paper abstract text")
    assert first.deduplicated is False

    second = store.put(b"paper abstract text")
    assert second.deduplicated is True
    assert second.digest == first.digest

    with store.open(first.digest) as f:
        assert f.read() == b"paper abstract text"

    assert store.delete(first.digest) is True
    assert store.exists(first.digest) is False
