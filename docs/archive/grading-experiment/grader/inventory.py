#!/usr/bin/env python3
"""Programmatic artifact inventory — hard facts the LLM must not hallucinate over.

Rubrics demand things that plain text extraction destroys: screenshots, diagrams,
tables, numbers. We count them mechanically and inject them as facts.
"""
import os, re, zipfile, json

IMG_MD   = re.compile(r"!\[[^\]]*\]\([^)]+\)")
IMG_REF  = re.compile(r"media/image|\.png|\.jpg|\.jpeg|\bimage\d+\b", re.I)
TABLE_MD = re.compile(r"^\s*\|.+\|\s*$", re.M)
LINK     = re.compile(r"https?://[^\s)\]>]+")
CODE     = re.compile(r"```[a-zA-Z]*\n")
DIAGRAM  = re.compile(r"```(mermaid|plantuml|puml)|@startuml|sequenceDiagram|flowchart|graph (TD|LR|TB)|C4Context|C4Container", re.I)
NUMBER   = re.compile(r"(?<![\w.])\d[\d\s.,]*(%|руб|₽|\$|тыс|млн|шт|дн|мин|сек|ч\b)")
FORMULA  = re.compile(r"[=×*/+-]\s*\d|\bCR\b|\bARPU\b|\bLTV\b|\bCAC\b|\bMDE\b|\balpha\b|\bбета\b", re.I)
TESTS    = re.compile(r"\bdef test_|\bfunc Test[A-Z]|@pytest|assert ", re.I)

def media_in_office(path):
    try:
        z = zipfile.ZipFile(path)
        return len([n for n in z.namelist() if "/media/" in n])
    except Exception:
        return 0

def inventory(text, source_files=()):
    """source_files: original paths, so we can count真 embedded media."""
    embedded = sum(media_in_office(f) for f in source_files
                   if f.lower().endswith((".docx", ".xlsx", ".pptx")))
    inv = {
        "chars": len(text),
        "words": len(text.split()),
        "images_embedded": embedded,
        "image_refs_in_text": len(IMG_MD.findall(text)) or len(set(IMG_REF.findall(text))),
        "tables": len(TABLE_MD.findall(text)) and text.count("\n|"),
        "table_rows": len(TABLE_MD.findall(text)),
        "links": len(set(LINK.findall(text))),
        "code_blocks": len(CODE.findall(text)),
        "diagrams": len(DIAGRAM.findall(text)),
        "quantities": len(NUMBER.findall(text)),
        "formula_like": len(FORMULA.findall(text)),
        "test_defs": len(TESTS.findall(text)),
        "headings": len(re.findall(r"^#{1,4} ", text, re.M)),
    }
    inv["has_visual_evidence"] = inv["images_embedded"] > 0 or inv["diagrams"] > 0
    return inv

def render(inv):
    """Human/LLM readable fact block."""
    return ("ОБЪЕКТИВНАЯ ОПИСЬ АРТЕФАКТОВ (посчитано программой, не оценка):\n"
            f"- символов: {inv['chars']}, слов: {inv['words']}, заголовков: {inv['headings']}\n"
            f"- встроенных изображений/скриншотов: {inv['images_embedded']}\n"
            f"- диаграмм (mermaid/plantuml/sequence): {inv['diagrams']}\n"
            f"- строк таблиц: {inv['table_rows']}, внешних ссылок: {inv['links']}\n"
            f"- блоков кода: {inv['code_blocks']}, определений тестов: {inv['test_defs']}\n"
            f"- числовых величин с единицами: {inv['quantities']}\n"
            "ВНИМАНИЕ: объём текста НЕ является признаком качества. "
            "Не повышай оценку за многословие.\n")

if __name__ == "__main__":
    import sys
    t = open(sys.argv[1]).read()
    print(json.dumps(inventory(t), ensure_ascii=False, indent=1))
