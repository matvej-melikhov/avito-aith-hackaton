#!/usr/bin/env python3
"""Normalize any homework artifact (docx/pdf/xlsx/ipynb/html/md/code) into plain text."""
import json, os, subprocess, sys, zipfile

MAXCHARS = 200_000
NEEDS_VISION = "[[NEEDS_VISION]]"

def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    return r.stdout

def docx(p):  return sh(["pandoc", "-t", "markdown", "--wrap=none", p])
def htm(p):   return sh(["pandoc", "-f", "html", "-t", "markdown", "--wrap=none", p])
def pdf(p):
    """PDF без текстового слоя (скан, экспорт из Miro/Figma) даёт пустую строку.
    Молча вернуть '' нельзя: работа получит 0 и уедет в конец рейтинга.
    Помечаем её как требующую vision и рендерим страницы рядом."""
    text = sh(["pdftotext", "-layout", p, "-"])
    if len(text.strip()) >= 200:
        return text
    try:
        import pymupdf
        doc = pymupdf.open(p)
        out = os.path.splitext(p)[0] + "__render"
        os.makedirs(out, exist_ok=True)
        pages = []
        for i, pg in enumerate(doc):
            pg.get_pixmap(dpi=110).save(f"{out}/p{i+1}.png")
            pages.append(f"{out}/p{i+1}.png")
        return (NEEDS_VISION + f"\nPDF без текстового слоя: {len(doc)} стр., "
                f"отрисовано в {out}\nСтраницы: " + ", ".join(pages) +
                "\nОценивать этот файл текстовой моделью нельзя — нужен vision-проход.")
    except Exception as e:
        return NEEDS_VISION + f"\nPDF без текстового слоя, рендер не удался: {e}"

def xlsx(p):
    import openpyxl
    try:
        wb = openpyxl.load_workbook(p, data_only=True)
    except Exception as e:
        return f"[xlsx unreadable: {e}]"
    out = []
    for ws in wb.worksheets:
        out.append(f"\n### SHEET: {ws.title}  ({ws.dimensions})")
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else str(c).replace("\n", " ⏎ ") for c in row]
            while cells and not cells[-1].strip():
                cells.pop()
            if any(c.strip() for c in cells):
                out.append(" | ".join(cells))
    return "\n".join(out)

def ipynb(p):
    nb = json.load(open(p, encoding="utf-8"))
    out = []
    for i, c in enumerate(nb.get("cells", [])):
        src = "".join(c.get("source", []))
        if not src.strip():
            continue
        out.append(f"\n--- cell {i} [{c.get('cell_type')}] ---\n{src}")
        for o in c.get("outputs", [])[:3]:
            t = "".join(o.get("text", [])) if "text" in o else ""
            if not t and "data" in o:
                t = "".join(o["data"].get("text/plain", []))
            if t:
                out.append(f"[output] {t[:1500]}")
    return "\n".join(out)

def plain(p):
    return open(p, encoding="utf-8", errors="replace").read()

HANDLERS = {".docx": docx, ".doc": docx, ".pdf": pdf, ".xlsx": xlsx, ".xls": xlsx,
            ".ipynb": ipynb, ".html": htm, ".htm": htm}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pyc", ".sum", ".DS_Store", ".zip", ".bin", ".pt", ".safetensors"}

def extract(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in SKIP_EXT:
        return None
    try:
        text = HANDLERS.get(ext, plain)(path)
    except Exception as e:
        return f"[extract failed: {e}]"
    return text[:MAXCHARS]

if __name__ == "__main__":
    print(extract(sys.argv[1]))
