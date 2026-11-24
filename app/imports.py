"""File types and path rules shared by single-file and folder registration."""

from pathlib import PurePosixPath

IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
EXTENSIONS = {".txt", ".md", ".markdown", ".pdf", ".docx", ".pptx", ".html", ".htm", *IMAGES}
IGNORED_DIRS = {"node_modules", "__pycache__", "site-packages", "hwp-reader"}


def relative_name(raw):
    raw = raw.replace("\\", "/")
    parts = raw.split("/")
    if any(not s or s in {".", ".."} or ":" in s or "\x00" in s for s in parts):
        raise ValueError("폴더 안의 상대 경로를 확인하세요.")
    return PurePosixPath(*parts)


def excluded(path):
    return (any(s.startswith(".") or s.casefold() in IGNORED_DIRS for s in path.parts[:-1])
            or path.name.startswith((".", "~$")) or path.suffix.lower() not in EXTENSIONS)


def read_text(path):
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp949")
