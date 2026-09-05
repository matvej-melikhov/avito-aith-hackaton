#!/usr/bin/env python3
"""Вклеивает tokens.css и components.css внутрь HTML-файлов пака.

Источник правды остаётся в двух CSS-файлах. HTML после сборки открывается
где угодно: почтой, ссылкой, перетаскиванием в браузер, без соседних файлов.

Запуск:  python3 docs/design/build.py
"""
import pathlib
import re

HERE = pathlib.Path(__file__).parent
SOURCES = ["tokens.css", "components.css"]
# У слайдов свой слой поверх общих компонентов.
TARGETS = {
    "kit.html": SOURCES,
    "screens.html": SOURCES,
    "deck.html": SOURCES + ["deck.css"],
}

START = "<!-- css:start -->"
END = "<!-- css:end -->"

def bundle(sources):
    css = "\n\n".join(
        "/* ===== %s ===== */\n%s" % (n, (HERE / n).read_text(encoding="utf-8").strip())
        for n in sources
    )
    return "%s\n<style>\n%s\n</style>\n%s" % (START, css, END)


for name, sources in TARGETS.items():
    block = bundle(sources)
    path = HERE / name
    text = path.read_text(encoding="utf-8")
    if START in text and END in text:
        text = re.sub(
            re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S
        )
    else:
        links = "\n".join('<link rel="stylesheet" href="%s">' % n for n in sources)
        if links not in text:
            raise SystemExit("%s: не нашёл ни маркеров, ни ссылок на CSS" % name)
        text = text.replace(links, block)
    path.write_text(text, encoding="utf-8")
    print("%s: стили вклеены, %d КБ" % (name, len(text.encode("utf-8")) // 1024))
