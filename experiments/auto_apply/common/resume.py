"""A one-page dummy PDF resume for the fictional test applicant, written by hand (no dependency)."""

from __future__ import annotations

from pathlib import Path

from .profile import PROFILE, RESUME_NAME

HERE = Path(__file__).resolve().parents[1]
RESUME_PATH = HERE / "data" / RESUME_NAME


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_resume() -> Path:
    lines = [
        "TEST RESUME - FICTIONAL PERSON - DO NOT PROCESS",
        PROFILE["full_name"],
        f"{PROFILE['email']}  |  {PROFILE['phone']}  |  {PROFILE['location']}",
        "",
        "Experience",
        "Software Engineer, Example Labs (2022 - now)",
        "Junior Developer, Sample Systems (2021 - 2022)",
        "",
        "Education",
        PROFILE["education"],
    ]
    ops = ["BT", "/F1 12 Tf", "14 TL", "56 780 Td"]
    for line in lines:
        ops.append(f"({_escape(line)}) Tj T*")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    RESUME_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESUME_PATH.write_bytes(bytes(out))
    return RESUME_PATH
