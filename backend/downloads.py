"""The Content-Disposition of a download, safe for any file name.

HTTP headers are latin-1: a Thai branch name in `filename=` raises UnicodeEncodeError and the
download answers 500 (the feedback daily report for one branch, 2026-10-26). RFC 6266 / 5987:
an ASCII `filename=` fallback plus `filename*=UTF-8''<percent-encoded>` carrying the real name,
which every current browser saves the file under. Same form as routers/bom_center.py's export.
"""
import re
from urllib.parse import quote


def attachment(filename: str) -> str:
    """`attachment; filename="<ascii>"; filename*=UTF-8''<name>` for `filename`."""
    name = str(filename or "download")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    ascii_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_.-")
    ascii_ext = re.sub(r"[^A-Za-z0-9]+", "", ext)
    fallback = (ascii_stem if re.search(r"[A-Za-z0-9]", ascii_stem) else "download") + (f".{ascii_ext}" if ascii_ext else "")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"
