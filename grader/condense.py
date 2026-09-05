#!/usr/bin/env python3
"""Структурная усадка работы под лимит контекста.

Обрезка «первые N символов» — систематическая ошибка, а не шум: она бьёт по
самым длинным работам и выкидывает ровно те разделы (выводы, анализ рисков,
контр-метрики), по которым и различается уровень. У notebook-экспортов первые
45 КБ — это импорты и boilerplate.

Здесь: сохраняем все заголовки, начало и конец каждого раздела, режем середину
и ЯВНО помечаем каждый пропуск, чтобы модель знала, что текст неполон.
"""
import re

HEAD = re.compile(r"^(#{1,4} .*|={3,} FILE: .*|--- cell \d+.*)$", re.M)

def split_sections(text):
    marks = [(m.start(), m.group(0)) for m in HEAD.finditer(text)]
    if not marks:
        return [("", text)]
    out = []
    if marks[0][0] > 0:
        out.append(("", text[:marks[0][0]]))
    for i, (pos, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        out.append((title, text[pos:end]))
    return out

def condense(text, budget=45_000, head_frac=0.45):
    """Ужимает text до budget символов, сохраняя структуру и концовки разделов."""
    if len(text) <= budget:
        return text
    secs = split_sections(text)
    n = len(secs)
    # бюджет пропорционально размеру раздела, но не меньше 400 символов на раздел
    total = sum(len(b) for _, b in secs)
    out, dropped = [], 0
    for title, body in secs:
        share = max(400, int(budget * len(body) / total))
        if len(body) <= share:
            out.append(body)
            continue
        h = int(share * head_frac)
        t = share - h
        cut = len(body) - h - t
        dropped += cut
        out.append(body[:h] + f"\n[…пропущено {cut} символов середины раздела…]\n" + body[-t:])
    res = "".join(out)
    if len(res) > budget:                      # страховка
        h = int(budget * 0.6); t = budget - h
        res = res[:h] + f"\n[…пропущено {len(res)-budget} символов…]\n" + res[-t:]
    return (f"[ВНИМАНИЕ: работа ужата с {len(text)} до ~{len(res)} символов; "
            f"пропуски помечены. Отсутствие раздела в тексте может быть следствием "
            f"усадки — не считай это невыполнением, если раздел упомянут в заголовках.]\n"
            + res)

if __name__ == "__main__":
    import sys
    t = open(sys.argv[1]).read()
    b = int(sys.argv[2]) if len(sys.argv) > 2 else 45_000
    c = condense(t, b)
    print(f"{len(t)} -> {len(c)}", file=sys.stderr)
    print(c[:1500])
