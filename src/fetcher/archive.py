"""Safe unpacking of archives (.zip, .tar, .gz, .tgz, .bz2, .xz) and choice of the main document."""
import bz2
import gzip
import io
import lzma
import os
import posixpath
import tarfile
import zipfile

MAX_FILES = int(os.getenv("ARCHIVE_MAX_FILES", "2000"))
MAX_TOTAL = int(os.getenv("ARCHIVE_MAX_MB", "500")) * 1024 * 1024
MAX_RATIO = 200                       # uncompressed / compressed — zip-bomb guard
MAX_DEPTH = 2                         # .tar inside .gz, etc.

FIGURE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp", ".webp")
# Main-document preference: JATS full text first, then PDF, then other text formats.
_PRIORITY = ((".nxml", "jats"), (".xml", "xml"), (".pdf", "pdf"), (".html", "html"), (".htm", "html"),
             (".docx", "docx"), (".epub", "epub"), (".md", "markdown"), (".txt", "text"))


class ArchiveError(ValueError):
    """Archive rejected by a safety limit or unreadable."""


def _safe_name(name: str):
    """Normalised member path, or None if it escapes the archive (absolute or '..')."""
    norm = posixpath.normpath(name.replace("\\", "/"))
    if norm.startswith(("/", "../")) or norm == ".." or ":" in norm.split("/")[0]:
        return None
    return norm


def _check(total: int, count: int, packed: int) -> None:
    if count > MAX_FILES:
        raise ArchiveError(f"too many files (> {MAX_FILES})")
    if total > MAX_TOTAL:
        raise ArchiveError(f"too large when unpacked (> {MAX_TOTAL // (1024 * 1024)} MB)")
    if packed and total > packed * MAX_RATIO and total > 10 * 1024 * 1024:
        raise ArchiveError("compression ratio too high (possible zip bomb)")


def _read_capped(stream, limit: int) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ArchiveError(f"too large when unpacked (> {MAX_TOTAL // (1024 * 1024)} MB)")
    return data


def unpack(data: bytes, fmt: str, filename: str = "", depth: int = 0) -> list:
    """[(path, bytes)] for every regular file; nested archives are unpacked too."""
    if depth > MAX_DEPTH:
        raise ArchiveError("archives nested too deeply")
    members, total = [], 0

    if fmt == "zip":
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise ArchiveError(f"unreadable zip: {e}")
        infos = [i for i in zf.infolist() if not i.is_dir()]
        _check(sum(i.file_size for i in infos), len(infos), len(data))
        for i in infos:
            name = _safe_name(i.filename)
            if name and not (i.external_attr >> 16) & 0o120000 == 0o120000:     # skip symlinks
                members.append((name, zf.read(i)))
    elif fmt == "tar":
        try:
            tf = tarfile.open(fileobj=io.BytesIO(data))
        except tarfile.TarError as e:
            raise ArchiveError(f"unreadable tar: {e}")
        infos = [m for m in tf.getmembers() if m.isfile()]          # links and devices are skipped
        _check(sum(m.size for m in infos), len(infos), len(data))
        for m in infos:
            name = _safe_name(m.name)
            if name:
                members.append((name, tf.extractfile(m).read()))
    elif fmt in ("gzip", "bz2", "xz"):
        opener = {"gzip": gzip.GzipFile, "bz2": bz2.BZ2File, "xz": lzma.LZMAFile}[fmt]
        try:
            inner = _read_capped(opener(fileobj=io.BytesIO(data)), MAX_TOTAL)
        except (OSError, EOFError, lzma.LZMAError) as e:
            raise ArchiveError(f"unreadable {fmt}: {e}")
        _check(len(inner), 1, len(data))
        base = os.path.basename(filename or "document")
        inner_name = base[:base.rfind(".")] if "." in base else base
        if base.lower().endswith(".tgz"):
            inner_name = base[:-4] + ".tar"
        members.append((inner_name, inner))
    else:
        raise ArchiveError(f"not an archive: {fmt}")

    from src.fetcher.format_detector import ARCHIVES, FormatDetector
    out = []
    for name, content in members:
        total += len(content)
        _check(total, len(out) + 1, len(data))
        kind = FormatDetector().detect(data=content, filename=name)
        if kind in ARCHIVES:
            out += [(f"{name}/{n}", c) for n, c in unpack(content, kind, name, depth + 1)]
        else:
            out.append((name, content))
    return out


def pick_main(members: list) -> tuple:
    """(path, bytes) of the main document and [(path, bytes)] figure files."""
    figures = [(n, c) for n, c in members if n.lower().endswith(FIGURE_EXT)]
    docs = [(n, c) for n, c in members if not n.lower().endswith(FIGURE_EXT)
            and not posixpath.basename(n).startswith((".", "__MACOSX")) and "__MACOSX/" not in n]
    for ext, kind in _PRIORITY:
        hits = [(n, c) for n, c in docs if n.lower().endswith(ext)]
        if kind == "xml":
            hits = [(n, c) for n, c in hits if b"<article" in c[:4000]]   # only JATS articles
        if hits:
            return max(hits, key=lambda m: len(m[1])), figures      # the largest is the full paper
    if docs:
        return max(docs, key=lambda m: len(m[1])), figures
    raise ArchiveError("no readable document in archive")
