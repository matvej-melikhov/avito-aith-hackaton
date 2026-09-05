"""Программная опись артефактов: факты, над которыми модель не должна фантазировать.

Из grader/inventory.py: рубрики требуют скриншоты, диаграммы, таблицы, числа, тесты.
Считаем механически и подаём модели как факты. Объём текста не признак качества.
"""

from __future__ import annotations

import re

from prereview.artifact.model import Work

IMG_MD = re.compile(r"!\[[^\]]*\]\([^)]+\)")
IMG_REF = re.compile(r"media/image|\.png|\.jpg|\.jpeg|\bimage\d+\b", re.I)
TABLE_MD = re.compile(r"^\s*\|.+\|\s*$", re.M)
LINK = re.compile(r"https?://[^\s)\]>]+")
CODE = re.compile(r"```[a-zA-Z]*\n")
DIAGRAM = re.compile(
    r"```(mermaid|plantuml|puml)|@startuml|sequenceDiagram|flowchart|graph (TD|LR|TB)|"
    r"C4Context|C4Container|C4Component|C4Deployment|erDiagram|classDiagram|stateDiagram",
    re.I,
)
C4_LEVELS = re.compile(r"\b(C4Context|C4Container|C4Component|C4Deployment|C4Dynamic)\b")
NUMBER = re.compile(r"(?<![\w.])\d[\d\s.,]*(%|руб|₽|\$|тыс|млн|шт|дн|мин|сек|ч\b)")
TESTS = re.compile(r"\bdef test_|\bfunc Test[A-Z]|@pytest|\bit\(|\btest\(|assert ", re.I)
HEADING = re.compile(r"^#{1,4} ", re.M)


def inventory(work: Work) -> dict:
    text = "\n".join(f.text for f in work.files)
    inv = {
        "format": work.format,
        "files": len(work.files),
        "chars": len(text),
        "words": len(text.split()),
        "images_embedded": int(work.meta.get("images_embedded", 0) or 0),
        "image_refs_in_text": len(IMG_MD.findall(text)) or len(set(IMG_REF.findall(text))),
        "table_rows": len(TABLE_MD.findall(text)),
        "links": len(set(LINK.findall(text))),
        "code_blocks": len(CODE.findall(text)),
        "diagrams": len(DIAGRAM.findall(text)),
        "c4_levels": sorted(set(C4_LEVELS.findall(text))),
        "quantities": len(NUMBER.findall(text)),
        "test_defs": len(TESTS.findall(text)),
        "headings": len(HEADING.findall(text)),
        "pages": work.meta.get("pages"),
        "languages": sorted({f.language for f in work.files if f.language}),
    }
    inv["has_visual_evidence"] = inv["images_embedded"] > 0 or inv["diagrams"] > 0
    return inv


def render(inv: dict) -> str:
    lines = [
        "ОПИСЬ АРТЕФАКТОВ (посчитано программой, это факты, а не оценка):",
        f"- файлов: {inv['files']}, символов: {inv['chars']}, слов: {inv['words']}, заголовков: {inv['headings']}",
        f"- встроенных изображений: {inv['images_embedded']}, ссылок на изображения в тексте: {inv['image_refs_in_text']}",
        f"- диаграмм (mermaid/plantuml/C4): {inv['diagrams']}, уровни C4: {', '.join(inv['c4_levels']) or 'нет'}",
        f"- строк таблиц: {inv['table_rows']}, внешних ссылок: {inv['links']}",
        f"- блоков кода: {inv['code_blocks']}, определений тестов: {inv['test_defs']}",
        f"- числовых величин с единицами: {inv['quantities']}",
    ]
    if inv.get("pages"):
        lines.append(f"- страниц PDF: {inv['pages']}")
    if inv.get("languages"):
        lines.append(f"- языки файлов: {', '.join(inv['languages'])}")
    lines.append("Объём текста не признак качества. Не повышай оценку за многословие.")
    return "\n".join(lines)
