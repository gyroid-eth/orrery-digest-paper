from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "digest-paper" / "scripts"


def png(path: Path, width: int = 8, height: int = 6) -> Path:
    """A tiny valid PNG, so tests need no image library and no real paper."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def runs_of(tmp_path: Path) -> Path:
    """Where prepare_input.py puts run folders in a test (see isolated_home)."""
    return tmp_path / "agentstack" / "addons" / "digest-paper" / "runs"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Run folders default to $AGENTSTACK_HOME; never let a test write to the
    real ~/.agentstack."""
    monkeypatch.setenv("AGENTSTACK_HOME", str(tmp_path / "agentstack"))


@pytest.fixture
def paper(tmp_path):
    """A made-up pdf-mistral style paper: a vault embed, a file URI to an
    outside image folder with a space and Japanese in the name, a relative
    link, a remote image and a missing one."""
    vault = tmp_path / "vault"
    images = vault / "MDPapers" / "pdf-mistral-images"
    outside = tmp_path / "MDPapers images"
    png(images / "Sample 2024_img-0.png", 10, 4)
    ext = png(outside / "Sample 2024_画像-1.png", 6, 6)
    png(vault / "MDPapers" / "rel" / "img-2.png", 5, 5)
    uri = ext.as_uri()
    md = vault / "MDPapers" / "Sample 2024.md"
    md.write_text(
        "# A made-up paper about gels\n\n"
        "Some text.\n\n"
        "![[MDPapers/pdf-mistral-images/Sample 2024_img-0.png|500]]\n\n"
        "Fig. 1 | A gel sheet folds into a cone.\n\n"
        f"![]({uri})\n\n"
        "Figure 2. Swelling ratio against light dose.\n\n"
        "![](rel/img-2.png)\n\n"
        "![](https://example.org/remote.png)\n\n"
        "![[MDPapers/pdf-mistral-images/missing_img-9.png]]\n",
        encoding="utf-8",
    )
    return {"vault": vault, "md": md, "outside": outside, "out": tmp_path / "out", "runs": runs_of(tmp_path)}
