"""Байты артефакта → Work. Форматы: markdown, docx (pandoc), pdf (pdftotext), zip (снимок GitHub).

Ничего не исполняет, в сеть не ходит. PDF без текстового слоя помечается флагом
needs_vision, а не оценивается как пустая работа.
"""

from __future__ import annotations

import io
import os
import posixpath
import subprocess
import tempfile
import zipfile
from pathlib import PurePosixPath

from prereview.artifact.model import CODE_EXT, Work, WorkFile

MEDIA_MARKDOWN = "text/markdown"
MEDIA_PDF = "application/pdf"
MEDIA_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MEDIA_ZIP = "application/zip"
SUPPORTED = {MEDIA_MARKDOWN, MEDIA_PDF, MEDIA_DOCX, MEDIA_ZIP, "text/plain"}

SKIP_DIRS = {".git", "vendor", "node_modules", "__pycache__", ".idea", ".vscode", "dist", "build",
             ".venv", "venv", "target", ".next", "coverage", ".pytest_cache", ".mypy_cache"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".pdf", ".zip", ".gz", ".tar",
            ".jar", ".class", ".o", ".so", ".dylib", ".dll", ".exe", ".bin", ".lock", ".pyc",
            ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".docx", ".xlsx", ".pptx", ".db", ".sqlite",
            ".pprof", ".prof", ".out", ".min.js", ".map"}
# Сгенерированные отчёты и артефакты сборки: не код студента, в срез не идут.
SKIP_NAMES = {"coverage.html", "coverage.out", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
SECRET_FILES = {".env"}


class UnsupportedFormat(ValueError):
    pass


class ExtractionError(ValueError):
    pass


def _run(cmd: list[str], data: bytes | None = None, timeout: int = 120) -> str:
    try:
        proc = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as e:
        raise ExtractionError(f"нет инструмента {cmd[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise ExtractionError(f"{cmd[0]} не уложился в {timeout} с") from e
    if proc.returncode != 0:
        raise ExtractionError(f"{cmd[0]} завершился с кодом {proc.returncode}: {proc.stderr[:300]!r}")
    return proc.stdout.decode("utf-8", errors="replace")


def _language(path: str) -> str | None:
    name = PurePosixPath(path).name.lower()
    if name in {"dockerfile", "makefile"}:
        return name
    return CODE_EXT.get(PurePosixPath(name).suffix)


def extract(data: bytes, media_type: str, *, filename: str = "", max_file_bytes: int = 200_000) -> Work:
    media = (media_type or "").split(";")[0].strip().lower()
    if media in {MEDIA_MARKDOWN, "text/plain"}:
        text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
        name = filename or "work.md"
        return Work("markdown", media, [WorkFile(name, text, "markdown")], meta={"chars": len(text)})
    if media == MEDIA_DOCX:
        return _docx(data, filename or "work.docx")
    if media == MEDIA_PDF:
        return _pdf(data, filename or "work.pdf")
    if media == MEDIA_ZIP:
        return _zip(data, max_file_bytes=max_file_bytes)
    raise UnsupportedFormat(f"формат {media_type!r} не поддерживается")


def _docx(data: bytes, filename: str) -> Work:
    images = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            images = sum(1 for n in z.namelist() if n.startswith("word/media/"))
    except zipfile.BadZipFile as e:
        raise ExtractionError("docx повреждён") from e
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.docx")
        with open(src, "wb") as fh:
            fh.write(data)
        text = _run(["pandoc", "-f", "docx", "-t", "gfm", "--wrap=none", src])
    text = text.replace("\r\n", "\n")
    work = Work("docx", MEDIA_DOCX, [WorkFile(filename, text, "markdown")],
                meta={"images_embedded": images, "chars": len(text)})
    if images:
        work.flags.append("has_images")
    return work


def _pdf(data: bytes, filename: str) -> Work:
    pages = None
    try:
        import fitz  # pymupdf

        with fitz.open(stream=data, filetype="pdf") as doc:
            pages = doc.page_count
    except Exception:  # noqa: BLE001 — счётчик страниц необязателен
        pages = None
    text = _run(["pdftotext", "-layout", "-", "-"], data=data).replace("\r\n", "\n")
    work = Work("pdf", MEDIA_PDF, [WorkFile(filename, text, "text")],
                meta={"pages": pages, "chars": len(text.strip())})
    if len(text.strip()) < 50:
        work.flags.append("needs_vision")
    return work


def _zip(data: bytes, *, max_file_bytes: int) -> Work:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ExtractionError("архив повреждён") from e
    names = [i.filename for i in z.infolist() if not i.is_dir()]
    if not names:
        raise ExtractionError("архив пуст")
    # Зипбол GitHub кладёт всё в одну верхнюю папку owner-repo-sha/: снимаем её.
    # В учебном корпусе репозиторий бывает вложен ещё раз, поэтому снимаем все
    # единственные верхние папки подряд, пока на уровне не появится второй элемент.
    root = ""
    while True:
        rest = [n[len(root):] for n in names if n.startswith(root)]
        first = {n.split("/", 1)[0] for n in rest}
        if len(first) == 1 and all("/" in n for n in rest):
            root += first.pop() + "/"
        else:
            break
    files: list[WorkFile] = []
    skipped: list[dict] = []
    secrets: list[str] = []
    for info in z.infolist():
        if info.is_dir():
            continue
        rel = info.filename[len(root):] if info.filename.startswith(root) else info.filename
        rel = posixpath.normpath(rel)
        if rel.startswith("..") or rel.startswith("/"):
            skipped.append({"path": info.filename, "why": "небезопасный путь"})
            continue
        parts = PurePosixPath(rel).parts
        if any(p in SKIP_DIRS for p in parts[:-1]):
            continue
        name = parts[-1]
        if name in SECRET_FILES or name.startswith(".env."):
            secrets.append(rel)
            continue
        if name in SKIP_NAMES or PurePosixPath(name).suffix.lower() in SKIP_EXT:
            skipped.append({"path": rel, "why": "бинарный или служебный формат"})
            continue
        if info.file_size > max_file_bytes:
            skipped.append({"path": rel, "why": f"больше {max_file_bytes} байт"})
            continue
        raw = z.read(info)
        if b"\x00" in raw[:4096]:
            skipped.append({"path": rel, "why": "бинарное содержимое"})
            continue
        text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
        files.append(WorkFile(rel, text, _language(rel)))
    files.sort(key=lambda f: f.path)
    work = Work("zip", MEDIA_ZIP, files, meta={
        "root": root.rstrip("/"), "files": len(files), "skipped": skipped,
        "secret_files_present": secrets, "chars": sum(f.size for f in files),
    })
    if secrets:
        work.flags.append("secret_file_present")
    if not files:
        work.flags.append("no_text_files")
    return work
