"""给 fileprobe 单测造字节。全部在内存里拼，不下载、不依赖外部样本文件。"""

import io
import zipfile


def minimal_pdf(text: str | None = None, *, version: str = "1.4") -> bytes:
    """手拼一个结构完整的最小 PDF（xref 偏移真算，不是糊的）。

    Args:
        text: 页面内容流里要画的文字；None 表示空白页（没有文本层）。
    """
    content = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET\n" if text else ""
    body = content.encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(f"%PDF-{version}\n".encode("ascii"))
    offsets: list[int] = []
    for index, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii") + payload + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
    out += f"startxref\n{xref_at}\n%%EOF\n".encode("ascii")
    return bytes(out)


def zip_with(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def epub_bytes() -> bytes:
    """epub 规范：第一个成员必须是未压缩存储的 mimetype。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"), b"application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        archive.writestr("META-INF/container.xml", b"<container/>")
    return buffer.getvalue()


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
GIF = b"GIF89a" + b"\x00" * 40
TIFF_LE = b"II*\x00" + b"\x00" * 40
TIFF_BE = b"MM\x00*" + b"\x00" * 40
BMP = b"BM" + b"\x00" * 40
GZIP = b"\x1f\x8b\x08\x00" + b"\x00" * 40
RTF = rb"{\rtf1\ansi hello}"
POSTSCRIPT = b"%!PS-Adobe-3.0\n"
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 40
