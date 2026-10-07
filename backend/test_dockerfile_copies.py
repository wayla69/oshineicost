"""Every directory main.py serves must reach the image: the Dockerfile copies
named paths only, so a new mount (pwa/ for the app manifest, #314) is a 404 on
Fly until its COPY line exists."""
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile"


def test_dockerfile_copies_every_served_directory():
    text = DOCKERFILE.read_text(encoding="utf-8")
    for d in ("js", "tabs", "vendor", "docs", "pwa", "backend"):
        assert f"COPY {d}/" in text, f"Dockerfile does not copy {d}/"
    assert "COPY index.html" in text
