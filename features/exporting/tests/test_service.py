import io
import json
import zipfile

import pytest

from caps.blobstore import BlobStore
from caps.blobstore.testing import MemoryBackend
from domain.attachments import create_attachment
from domain.works import add_identifier, create_work
from features.exporting.service import (
    DEFAULT_CITATION_STYLE,
    cite_formatted,
    cite_keys,
    cite_latex,
    cite_record_text,
    export_bibliography,
    export_with_attachments_zip,
    resolve_citation_style,
    set_default_citation_style,
)

LIBRARY = 1
OTHER_LIBRARY = 2
ACCOUNT = 1
OTHER_ACCOUNT = 2


def _work(db, *, title, library_id=LIBRARY, year=2020, doi=None):
    work = create_work(db, library_id=library_id, title=title, year=year)
    if doi:
        add_identifier(db, library_id=library_id, work_id=work.id, scheme="doi", value=doi)
    return work


# ── export_bibliography ──────────────────────────────────────────────────────


def test_export_bibliography_produces_requested_format(db):
    a = _work(db, title="Attention Is All You Need", doi="10.1000/aiayn")
    b = _work(db, title="Deep Residual Learning", doi="10.1000/resnet")

    text = export_bibliography(db, library_id=LIBRARY, work_ids=[a.id, b.id], format="csljson")
    items = json.loads(text)

    assert {item["title"] for item in items} == {"Attention Is All You Need", "Deep Residual Learning"}
    assert {item["DOI"] for item in items} == {"10.1000/aiayn", "10.1000/resnet"}


def test_export_bibliography_scopes_to_library_and_given_ids(db):
    mine = _work(db, title="Mine", library_id=LIBRARY)
    foreign = _work(db, title="Foreign", library_id=OTHER_LIBRARY)

    text = export_bibliography(db, library_id=LIBRARY, work_ids=[mine.id, foreign.id], format="csljson")
    items = json.loads(text)

    assert [item["title"] for item in items] == ["Mine"]


# ── export_with_attachments_zip ──────────────────────────────────────────────


def test_export_with_attachments_zip_includes_bibliography_and_file(db):
    store = BlobStore(MemoryBackend())
    work = _work(db, title="Gene Expression Study", doi="10.1000/gxp")
    put_result = store.put(b"%PDF-1.4 fake pdf bytes")
    create_attachment(
        db,
        library_id=LIBRARY,
        work_id=work.id,
        filename="original.pdf",
        digest=put_result.blob.digest,
        size=put_result.blob.size,
    )

    data = export_with_attachments_zip(
        db, library_id=LIBRARY, work_ids=[work.id], format="csljson", blob_store=store
    )

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert "bibliography.json" in names
        pdf_entries = [n for n in names if n.startswith("attachments/") and n.endswith(".pdf")]
        assert len(pdf_entries) == 1
        assert zf.read(pdf_entries[0]) == b"%PDF-1.4 fake pdf bytes"
        bib_items = json.loads(zf.read("bibliography.json"))
        assert bib_items[0]["title"] == "Gene Expression Study"


def test_export_with_attachments_zip_dedupes_filename_collisions(db):
    store = BlobStore(MemoryBackend())
    work_a = _work(db, title="Same Title", year=2020)
    work_b = _work(db, title="Same Title", year=2020)
    for work in (work_a, work_b):
        put_result = store.put(f"content for {work.id}".encode())
        create_attachment(
            db,
            library_id=LIBRARY,
            work_id=work.id,
            filename="paper.pdf",
            digest=put_result.blob.digest,
            size=put_result.blob.size,
        )

    data = export_with_attachments_zip(
        db, library_id=LIBRARY, work_ids=[work_a.id, work_b.id], format="csljson", blob_store=store
    )

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        pdf_entries = sorted(n for n in zf.namelist() if n.endswith(".pdf"))
        assert len(pdf_entries) == 2
        assert pdf_entries[0] != pdf_entries[1]
        contents = {zf.read(n) for n in pdf_entries}
        assert contents == {f"content for {work_a.id}".encode(), f"content for {work_b.id}".encode()}


# ── cite_* ────────────────────────────────────────────────────────────────────


def test_cite_record_text_is_a_single_record(db):
    work = _work(db, title="Solo Record", doi="10.1000/solo")

    text = cite_record_text(db, work_id=work.id, format="csljson")
    items = json.loads(text)

    assert len(items) == 1
    assert items[0]["title"] == "Solo Record"


def test_cite_formatted_includes_title_and_year(db):
    work = _work(db, title="Formatted Citation Example", year=2022)

    apa = cite_formatted(db, work_id=work.id, style="apa")
    vancouver = cite_formatted(db, work_id=work.id, style="vancouver")

    assert "Formatted Citation Example" in apa
    assert "2022" in apa
    assert "Formatted Citation Example" in vancouver
    assert apa != vancouver


def test_cite_keys_dedupes_within_the_same_batch(db):
    work_a = _work(db, title="Attention Mechanisms", year=2019)
    work_b = _work(db, title="Attention Mechanisms", year=2019)

    keys = cite_keys(db, work_ids=[work_a.id, work_b.id])

    assert len(set(keys.values())) == 2
    assert keys[work_b.id].startswith(keys[work_a.id])


def test_cite_latex_wraps_generated_keys_with_given_command(db):
    work_a = _work(db, title="Paper A", year=2020)
    work_b = _work(db, title="Paper B", year=2021)

    latex = cite_latex(db, work_ids=[work_a.id, work_b.id], command="citep")
    expected_keys = cite_keys(db, work_ids=[work_a.id, work_b.id])

    assert latex == r"\citep{" + ",".join(expected_keys.values()) + "}"


# ── 引用样式设置 ────────────────────────────────────────────────────────────


def test_resolve_citation_style_defaults_then_honors_account_override(db):
    assert resolve_citation_style(db, account_id=ACCOUNT) == DEFAULT_CITATION_STYLE

    set_default_citation_style(db, account_id=ACCOUNT, style="vancouver")

    assert resolve_citation_style(db, account_id=ACCOUNT) == "vancouver"
    assert resolve_citation_style(db, account_id=OTHER_ACCOUNT) == DEFAULT_CITATION_STYLE


def test_set_default_citation_style_rejects_unknown_style(db):
    with pytest.raises(ValueError, match="引用样式"):
        set_default_citation_style(db, account_id=ACCOUNT, style="mla")
