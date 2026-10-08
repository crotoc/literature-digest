"""caps/blobstore 单测。不碰文件系统——后端用内存替身，正好证明本模块
确实不知道字节落在哪。"""

import hashlib
import io

import pytest

from caps.blobstore import (
    ALGORITHM,
    Backend,
    Blob,
    BlobNotFound,
    BlobStore,
    DigestMismatch,
    InvalidDigest,
    digest_bytes,
    key_for,
    validate_digest,
)
from caps.blobstore.testing import MemoryBackend

PAYLOAD = b"%PDF-1.4 pretend this is a paper\n"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


@pytest.fixture
def backend() -> MemoryBackend:
    return MemoryBackend()


@pytest.fixture
def store(backend: MemoryBackend) -> BlobStore:
    return BlobStore(backend)


class TestDigestValidation:
    def test_accepts_lowercase_hex(self):
        digest = digest_bytes(PAYLOAD)
        assert validate_digest(digest) == digest

    def test_normalizes_case_and_whitespace(self):
        digest = digest_bytes(PAYLOAD)
        assert validate_digest(f"  {digest.upper()}  ") == digest

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "abc",
            "z" * 64,
            "a" * 63,
            "a" * 65,
            "../../etc/passwd",
            "a" * 32 + "/" + "a" * 31,
            "a" * 60 + "\n" + "aaa",
        ],
    )
    def test_rejects_malformed(self, bad):
        with pytest.raises(InvalidDigest):
            validate_digest(bad)

    def test_rejects_non_string(self):
        with pytest.raises(InvalidDigest):
            validate_digest(None)

    def test_traversal_cannot_reach_key(self):
        """摘要是拼进路径的，所以它必须先过校验——这是路径穿越的防线。"""
        for attack in ["../../../etc/passwd", "a/../../b", "." * 64]:
            with pytest.raises(InvalidDigest):
                key_for(attack)


class TestKeyLayout:
    def test_two_level_fanout(self):
        digest = "ab" + "cd" + "e" * 60
        assert key_for(digest) == f"{ALGORITHM}/ab/cd/{digest}"

    def test_key_starts_with_algorithm(self):
        assert key_for(digest_bytes(PAYLOAD)).startswith(f"{ALGORITHM}/")

    def test_blob_exposes_key(self):
        digest = digest_bytes(PAYLOAD)
        assert Blob(digest=digest, size=1).key == key_for(digest)

    def test_key_is_stable(self):
        digest = digest_bytes(PAYLOAD)
        assert key_for(digest) == key_for(digest.upper())


class TestPut:
    def test_returns_digest_and_size(self, store):
        result = store.put(PAYLOAD)
        assert result.digest == hashlib.sha256(PAYLOAD).hexdigest()
        assert result.size == len(PAYLOAD)
        assert result.deduplicated is False

    def test_content_lands_in_backend(self, store, backend):
        result = store.put(PAYLOAD)
        assert backend.blobs[key_for(result.digest)] == PAYLOAD

    def test_empty_content(self, store):
        result = store.put(b"")
        assert result.digest == EMPTY_SHA256
        assert result.size == 0
        assert store.exists(result.digest)

    def test_large_content_spans_chunks(self, store):
        payload = b"x" * ((1 << 20) * 2 + 7)
        result = store.put(payload)
        assert result.size == len(payload)
        assert store.verify(result.digest)

    def test_accepts_bytearray(self, store):
        assert store.put(bytearray(PAYLOAD)).digest == digest_bytes(PAYLOAD)

    def test_accepts_path(self, store, tmp_path):
        path = tmp_path / "a.pdf"
        path.write_bytes(PAYLOAD)
        assert store.put(path).digest == digest_bytes(PAYLOAD)
        assert store.put(str(path)).deduplicated is True

    def test_accepts_stream(self, store):
        assert store.put(io.BytesIO(PAYLOAD)).digest == digest_bytes(PAYLOAD)

    def test_accepts_non_seekable_stream(self, store):
        """和 fileprobe 不同：这里只往前读一遍，不需要可 seek。"""

        class Forward(io.RawIOBase):
            def __init__(self, data: bytes) -> None:
                self._data = data
                self._at = 0

            def readable(self) -> bool:
                return True

            def seekable(self) -> bool:
                return False

            def read(self, size=-1):
                chunk = self._data[self._at :] if size < 0 else self._data[self._at : self._at + size]
                self._at += len(chunk)
                return chunk

        assert store.put(Forward(PAYLOAD)).digest == digest_bytes(PAYLOAD)

    def test_rejects_wrong_type(self, store):
        with pytest.raises(TypeError):
            store.put(12345)


class TestDeduplication:
    def test_second_put_is_deduplicated(self, store):
        first = store.put(PAYLOAD)
        second = store.put(PAYLOAD)
        assert first.digest == second.digest
        assert first.deduplicated is False
        assert second.deduplicated is True

    def test_only_one_copy_stored(self, store, backend):
        store.put(PAYLOAD)
        store.put(PAYLOAD)
        store.put(io.BytesIO(PAYLOAD))
        assert len(backend.blobs) == 1

    def test_different_content_different_blob(self, store, backend):
        store.put(PAYLOAD)
        store.put(PAYLOAD + b"!")
        assert len(backend.blobs) == 2

    def test_no_staging_leak_after_dedup(self, store, backend):
        store.put(PAYLOAD)
        store.put(PAYLOAD)
        assert backend.pending_count == 0


class TestExpectedDigest:
    def test_matching_digest_accepted(self, store):
        digest = digest_bytes(PAYLOAD)
        assert store.put(PAYLOAD, expected_digest=digest).digest == digest

    def test_uppercase_expected_digest_accepted(self, store):
        store.put(PAYLOAD, expected_digest=digest_bytes(PAYLOAD).upper())

    def test_mismatch_raises(self, store):
        with pytest.raises(DigestMismatch) as caught:
            store.put(PAYLOAD, expected_digest="0" * 64)
        assert caught.value.expected == "0" * 64
        assert caught.value.actual == digest_bytes(PAYLOAD)

    def test_mismatch_leaves_nothing_behind(self, store, backend):
        with pytest.raises(DigestMismatch):
            store.put(PAYLOAD, expected_digest="0" * 64)
        assert backend.blobs == {}
        assert backend.pending_count == 0

    def test_malformed_expected_digest_rejected_before_reading(self, store, backend):
        with pytest.raises(InvalidDigest):
            store.put(PAYLOAD, expected_digest="nope")
        assert backend.write_calls == 0


class TestRead:
    def test_open_returns_content(self, store):
        digest = store.put(PAYLOAD).digest
        with store.open(digest) as stream:
            assert stream.read() == PAYLOAD

    def test_open_missing_raises(self, store):
        with pytest.raises(BlobNotFound) as caught:
            store.open("a" * 64)
        assert caught.value.digest == "a" * 64

    def test_open_malformed_raises_invalid(self, store):
        with pytest.raises(InvalidDigest):
            store.open("nope")

    def test_exists(self, store):
        digest = store.put(PAYLOAD).digest
        assert store.exists(digest) is True
        assert store.exists("b" * 64) is False

    def test_stat(self, store):
        digest = store.put(PAYLOAD).digest
        blob = store.stat(digest)
        assert blob == Blob(digest=digest, size=len(PAYLOAD))

    def test_stat_missing_returns_none(self, store):
        assert store.stat("c" * 64) is None

    def test_verify_ok(self, store):
        assert store.verify(store.put(PAYLOAD).digest) is True

    def test_verify_detects_corruption(self, store, backend):
        digest = store.put(PAYLOAD).digest
        backend.blobs[key_for(digest)] = b"tampered"
        assert store.verify(digest) is False

    def test_verify_missing_raises(self, store):
        with pytest.raises(BlobNotFound):
            store.verify("d" * 64)


class TestDelete:
    def test_delete_removes(self, store):
        digest = store.put(PAYLOAD).digest
        assert store.delete(digest) is True
        assert store.exists(digest) is False

    def test_delete_missing_is_false_not_error(self, store):
        assert store.delete("e" * 64) is False

    def test_delete_ignores_references(self, store):
        """本模块**不查引用计数**——这是刻意的，引用关系需要表，不在这一层。
        调用方必须自己先确认没人指着它。"""
        digest = store.put(PAYLOAD).digest
        assert store.delete(digest) is True

    def test_put_after_delete_writes_again(self, store):
        digest = store.put(PAYLOAD).digest
        store.delete(digest)
        assert store.put(PAYLOAD).deduplicated is False


class TestBackendContract:
    def test_memory_backend_satisfies_protocol(self):
        assert isinstance(MemoryBackend(), Backend)

    def test_store_only_talks_to_backend(self, backend):
        """BlobStore 自己不做任何 IO：换个后端，行为照旧。"""
        store = BlobStore(backend)
        digest = store.put(PAYLOAD).digest
        assert set(backend.blobs) == {key_for(digest)}

    def test_backend_failure_propagates_and_aborts(self, backend):
        class Boom(MemoryBackend):
            def finalize(self, pending, key):
                raise OSError("disk full")

        broken = Boom()
        store = BlobStore(broken)
        with pytest.raises(OSError, match="disk full"):
            store.put(PAYLOAD)
        assert broken.pending_count == 0
        assert broken.blobs == {}
