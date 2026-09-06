#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Собирает pptx защиты из вёрстки deck-b.html.

Палитра и структура повторяют docs/defense/deck-b.html. Шрифт взят
безопасный: Unbounded и Onest не установлены ни здесь, ни, скорее всего,
на машине, с которой будут показывать. Подставлять их вслепую значит
получить чужую подстановку и поехавшую вёрстку прямо на защите.

Запуск: python3 docs/defense/build_pptx.py
"""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

# --- палитра, значения из docs/design/tokens.css ---------------------------
GREEN  = RGBColor(0x04, 0xE0, 0x61)
INK    = RGBColor(0x1A, 0x1A, 0x1A)
WHITE  = RGBColor(0xFF, 0xFF, 0xFF)
S2     = RGBColor(0xF7, 0xF7, 0xF7)
S3     = RGBColor(0xEF, 0xEF, 0xEF)
INK2   = RGBColor(0x4A, 0x4A, 0x4A)
INK3   = RGBColor(0x74, 0x75, 0x74)
INK4   = RGBColor(0xA9, 0xAA, 0xA9)
LINE   = RGBColor(0xED, 0xEE, 0xEF)
RED    = RGBColor(0xC8, 0x20, 0x2F)
ORANGE = RGBColor(0xB8, 0x5A, 0x00)
VIOLET = RGBColor(0x6B, 0x36, 0xC4)

F = "Arial"

SW, SH = Inches(13.333), Inches(7.5)
M = Inches(0.75)          # поле слайда
CW = SW - 2 * M           # рабочая ширина

prs = Presentation()
prs.slide_width, prs.slide_height = SW, SH
BLANK = prs.slide_layouts[6]


def slide(bg=WHITE):
    s = prs.slides.add_slide(BLANK)
    r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, SW, SH)
    r.fill.solid(); r.fill.fore_color.rgb = bg
    r.line.fill.background(); r.shadow.inherit = False
    return s


def box(s, x, y, w, h, text, size=16, color=INK, bold=False, align=PP_ALIGN.LEFT,
        space=6, line=None, caps=False, anchor=MSO_ANCHOR.TOP):
    tb = s.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    for i, part in enumerate(text.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(space)
        if line:
            p.line_spacing = line
        run = p.add_run()
        run.text = part.upper() if caps else part
        run.font.size = Pt(size); run.font.bold = bold
        run.font.color.rgb = color; run.font.name = F
    return tb


def rect(s, x, y, w, h, fill=S2, line_col=None):
    r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    if fill is None:
        r.fill.background()
    else:
        r.fill.solid(); r.fill.fore_color.rgb = fill
    if line_col is None:
        r.line.fill.background()
    else:
        r.line.color.rgb = line_col; r.line.width = Pt(1)
    r.shadow.inherit = False
    return r


def pill(s, x, y, text, fill=S3, color=INK, size=11):
    w = Inches(0.13) * len(text) + Inches(0.34)
    h = Inches(0.32)
    r = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    r.adjustments[0] = 0.5
    r.fill.solid(); r.fill.fore_color.rgb = fill
    r.line.fill.background(); r.shadow.inherit = False
    tf = r.text_frame; tf.word_wrap = False
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    run = p.add_run(); run.text = text
    run.font.size = Pt(size); run.font.color.rgb = color; run.font.name = F
    return x + w + Inches(0.12)


def kicker(s, text, color=INK3):
    box(s, M, Inches(0.5), CW, Inches(0.3), text, size=11, color=color, caps=True, space=0)


def foot(s, left, right, color=INK3):
    box(s, M, SH - Inches(0.62), CW - Inches(1.2), Inches(0.3), left, size=10, color=color, space=0)
    box(s, SW - M - Inches(1.0), SH - Inches(0.62), Inches(1.0), Inches(0.3), right,
        size=10, color=color, align=PP_ALIGN.RIGHT, space=0)


# =========================== 01. Продукт и проблема ==========================
s = slide()
kicker(s, "Avito AI Reviewer")
box(s, M, Inches(1.05), Inches(7.4), Inches(2.2),
    "Проверка домашних работ\nупирается в людей", size=40, bold=True, line=0.95, space=0)
box(s, M, Inches(3.25), Inches(7.0), Inches(1.5),
    "Мы делаем помощник ревьюера: он разбирает работу по требованиям задания, "
    "на каждый вердикт приводит цитату из текста и отдаёт готовый черновик. "
    "Оценку ставит человек.", size=15, color=INK2, line=1.35)
x = M
x = pill(s, x, Inches(4.9), "~100 студентов на поток", fill=INK, color=WHITE)
x = pill(s, x, Inches(4.9), "~60 минут на одну работу", fill=GREEN, color=INK)
x = pill(s, x, Inches(4.9), "2 дня на весь поток", fill=S3, color=INK2)
box(s, M, Inches(5.42), Inches(6.0), Inches(0.3),
    "замер, интервью с ревьюером Tech QA", size=10, color=ORANGE, space=0)

# знак цитаты: чёрные скобки и зелёный круг, повторяет иллюстрацию 01-focus
cx, cy = Inches(9.15), Inches(1.9)
rect(s, cx, cy, Inches(0.22), Inches(3.0), fill=INK)
rect(s, cx, cy, Inches(0.75), Inches(0.22), fill=INK)
rect(s, cx, cy + Inches(2.78), Inches(0.75), Inches(0.22), fill=INK)
bx = cx + Inches(3.35)
rect(s, bx, cy, Inches(0.22), Inches(3.0), fill=INK)
rect(s, bx - Inches(0.53), cy, Inches(0.75), Inches(0.22), fill=INK)
rect(s, bx - Inches(0.53), cy + Inches(2.78), Inches(0.75), Inches(0.22), fill=INK)
circ = s.shapes.add_shape(MSO_SHAPE.OVAL, cx + Inches(0.62), cy + Inches(0.42), Inches(2.16), Inches(2.16))
circ.fill.solid(); circ.fill.fore_color.rgb = GREEN
circ.line.fill.background(); circ.shadow.inherit = False
box(s, cx + Inches(0.62), cy + Inches(0.40), Inches(2.16), Inches(2.0), "”",
    size=88, bold=True, color=WHITE, align=PP_ALIGN.CENTER, space=0,
    anchor=MSO_ANCHOR.MIDDLE)

foot(s, "Avito AI Reviewer", "01")
s.notes_slide.notes_text_frame.text = (
    "Курс набирает около ста студентов. Ревьюер тратит на одну работу примерно час, "
    "а весь поток нужно проверить за два дня. Это упирается не в мотивацию, а в арифметику. "
    "Мы сделали помощник, который разбирает работу по требованиям задания и на каждый вывод "
    "показывает место в тексте. Решение остаётся за человеком."
)

# ================================ 02. Ценность ==============================
s = slide()
kicker(s, "Ценность")
box(s, M, Inches(1.0), Inches(9.5), Inches(1.4),
    "Ускорение уже существует.\nОно живёт в голове одного человека",
    size=32, bold=True, line=1.02, space=0)
box(s, M, Inches(2.5), Inches(9.2), Inches(0.9),
    "На курсе Tech QA ревьюер собрал себе навык для модели и стал закрывать работу "
    "за 10–15 минут вместо часа. Это не наша гипотеза, а факт с курса.",
    size=15, color=INK2, line=1.35)

cw, gap = Inches(3.75), Inches(0.35)
cy, ch = Inches(3.7), Inches(2.75)
# было
rect(s, M, cy, cw, ch, fill=S2)
box(s, M + Inches(0.3), cy + Inches(0.28), cw - Inches(0.6), Inches(0.3), "было",
    size=10, color=INK3, caps=True, space=0)
box(s, M + Inches(0.3), cy + Inches(0.62), cw - Inches(0.6), Inches(1.0), "~60",
    size=54, bold=True, color=RED, space=0, line=0.9)
box(s, M + Inches(0.3), cy + Inches(1.62), cw - Inches(0.6), Inches(0.7),
    "минут на работу, полностью вручную", size=13, color=INK2, line=1.3)
box(s, M + Inches(0.3), cy + Inches(2.25), cw - Inches(0.6), Inches(0.3), "замер",
    size=10, color=ORANGE, space=0)
# стало
x2 = M + cw + gap
rect(s, x2, cy, cw, ch, fill=GREEN)
box(s, x2 + Inches(0.3), cy + Inches(0.28), cw - Inches(0.6), Inches(0.3),
    "стало у одного ревьюера", size=10, color=INK, caps=True, space=0)
box(s, x2 + Inches(0.3), cy + Inches(0.62), cw - Inches(0.6), Inches(1.0), "10–15",
    size=54, bold=True, color=INK, space=0, line=0.9)
box(s, x2 + Inches(0.3), cy + Inches(1.62), cw - Inches(0.6), Inches(0.7),
    "минут, личный навык для модели", size=13, color=INK, line=1.3)
box(s, x2 + Inches(0.3), cy + Inches(2.25), cw - Inches(0.6), Inches(0.3), "замер",
    size=10, color=INK, space=0)
# что делаем
x3 = x2 + cw + gap
rect(s, x3, cy, cw, ch, fill=INK)
box(s, x3 + Inches(0.3), cy + Inches(0.28), cw - Inches(0.6), Inches(0.3),
    "что делаем мы", size=10, color=GREEN, caps=True, space=0)
box(s, x3 + Inches(0.3), cy + Inches(0.66), cw - Inches(0.6), Inches(0.9),
    "Превращаем навык\nв платформу", size=20, bold=True, color=WHITE, line=1.1, space=0)
box(s, x3 + Inches(0.3), cy + Inches(1.72), cw - Inches(0.6), Inches(0.9),
    "Требования как данные, цитата на каждый вердикт, журнал решений. "
    "Работает на любом курсе и не зависит от одного человека.",
    size=12, color=INK4, line=1.3)

foot(s, "Год назад ту же идею пробовали на слабых моделях, стало медленнее, чем руками", "02")
s.notes_slide.notes_text_frame.text = (
    "Ценность уже доказана, но не нами. Один ревьюер на курсе Tech QA написал себе навык "
    "для модели и стал проверять работу за десять-пятнадцать минут вместо часа. Год назад "
    "та же попытка на слабых моделях провалилась, было медленнее, чем руками. Наша работа "
    "в том, чтобы этот навык перестал быть личным."
)


def chain(s, items, top, height=Inches(2.5)):
    """Цепочка шагов. Последний элемент можно выделить чёрным."""
    n = len(items)
    gap = Inches(0.12)
    w = (CW - gap * (n - 1)) / n
    for i, (num, head, body, on) in enumerate(items):
        x = M + (w + gap) * i
        rect(s, x, top, w, height, fill=INK if on else S2)
        box(s, x + Inches(0.25), top + Inches(0.24), w - Inches(0.5), Inches(0.28), num,
            size=10, color=GREEN if on else INK3, caps=True, space=0)
        box(s, x + Inches(0.25), top + Inches(0.6), w - Inches(0.5), Inches(0.75), head,
            size=15, bold=True, color=WHITE if on else INK, line=1.12, space=0)
        box(s, x + Inches(0.25), top + Inches(1.42), w - Inches(0.5), Inches(0.95), body,
            size=11.5, color=INK4 if on else INK2, line=1.3)


# ============================== 03. Карта демо ==============================
s = slide()
kicker(s, "Демо, один сквозной сценарий")
box(s, M, Inches(1.0), Inches(10.5), Inches(1.3),
    "Одна работа проходит весь путь:\nот сдачи до подтверждённой оценки",
    size=32, bold=True, line=1.02, space=0)
chain(s, [
    ("студент", "Сдаёт и проверяет себя",
     "Приходит по ссылке из Stepik, видит незакрытые требования до сдачи", False),
    ("пул", "Ревьюер берёт работу",
     "Никто ничего не раскладывает, работа закрепляется одним нажатием", False),
    ("разбор", "Модель показывает места",
     "По каждому требованию вердикт, цитата и строки в файле", False),
    ("решение", "Человек подтверждает",
     "Соглашается, меняет оценку, правит ответ студенту", True),
], Inches(2.85))
box(s, M, Inches(5.75), Inches(9.0), Inches(0.6),
    "Смотрим за одной работой. Все четыре шага идут подряд, без перескоков во времени "
    "и без обхода остальных экранов.", size=13, color=INK2, line=1.35)
foot(s, "Avito AI Reviewer", "03")
s.notes_slide.notes_text_frame.text = (
    "Сейчас покажу одну работу, которая пройдёт весь путь. Студент сдаёт и до сдачи сам "
    "видит, чего не хватает. Ревьюер берёт эту работу из общего пула. Система показывает "
    "разбор по требованиям с цитатами. Ревьюер подтверждает результат. Это один сценарий "
    "целиком, а не обход функций."
)

# ============================== 04. Демо, запись ============================
s = slide(bg=INK)
circ = s.shapes.add_shape(MSO_SHAPE.OVAL,
                          SW / 2 - Inches(0.85), SH / 2 - Inches(1.15), Inches(1.7), Inches(1.7))
circ.fill.solid(); circ.fill.fore_color.rgb = GREEN
circ.line.fill.background(); circ.shadow.inherit = False
tri = s.shapes.add_shape(MSO_SHAPE.ISOSCELES_TRIANGLE,
                         SW / 2 - Inches(0.24), SH / 2 - Inches(0.62), Inches(0.62), Inches(0.64))
tri.rotation = 90
tri.fill.solid(); tri.fill.fore_color.rgb = INK
tri.line.fill.background(); tri.shadow.inherit = False
box(s, M, SH / 2 + Inches(0.85), CW, Inches(0.5),
    "demo-full-run.mp4 · 1:50 · без звука, спикер комментирует вживую".replace(" · ", ", "),
    size=13, color=INK4, align=PP_ALIGN.CENTER, space=0)
foot(s, "Вставить запись демо вместо этой заглушки", "04", color=INK4)
s.notes_slide.notes_text_frame.text = (
    "ПЛЕЙСХОЛДЕР. Ролик ещё не снят. Записать один проход по сценарию слайда 03, без звука, "
    "курсор двигается медленно, паузы там, где говорит спикер. Длительность 1:50, "
    "16 на 9, не ниже 1920 на 1080. Комментарий спикера идёт по таймкодам резервного слайда."
)

# ================================ 05. Техника ===============================
s = slide()
kicker(s, "Как устроена проверка")
box(s, M, Inches(1.0), Inches(10.5), Inches(1.3),
    "Модель не может сослаться на то,\nчего студент не писал",
    size=32, bold=True, line=1.02, space=0)
chain(s, [
    ("01", "Требования как данные",
     "Критерии со шкалами и условием «что считается выполненным»", False),
    ("02", "Вызов на каждое требование",
     "Отдельный запрос по каждому пункту, а не один общий «оцени работу»", False),
    ("03", "Цитату сверяет код",
     "Нет фрагмента в работе — вердикт отклонён и не показан. Это гейт", True),
    ("04", "Человек и журнал",
     "Балл ставит ревьюер, каждая правка пишется в журнал", False),
], Inches(2.85))
rect(s, M, Inches(5.65), CW, Inches(0.95), fill=None, line_col=LINE)
box(s, M + Inches(0.28), Inches(5.85), Inches(2.2), Inches(0.3),
    "ограничения называем сами", size=10, color=INK3, caps=True, space=0)
box(s, M + Inches(2.7), Inches(5.85), CW - Inches(3.0), Inches(0.6),
    "Оценочные критерии модель только советует. Открытые модели на этой задаче дают плохой "
    "результат, берём фронтир. Влияние на успеваемость за неделю не проверяли.",
    size=12, color=INK2, line=1.3)
foot(s, "Контрольный вариант для сравнения: те же работы одним общим промптом", "05")
s.notes_slide.notes_text_frame.text = (
    "Главное отличие от чата с моделью в третьем звене. Модель обязана привести фрагмент "
    "работы, и код проверяет, что этот фрагмент в работе действительно есть. Не нашлось — "
    "вердикт отклоняется и не показывается ревьюеру. Поэтому разбор можно проверить "
    "за секунды, а не перечитывать работу целиком."
)


# ============================== 06. Результаты ==============================
s = slide()
kicker(s, "Что измерено")
box(s, M, Inches(1.0), Inches(10.5), Inches(1.3),
    "Пороги объявлены до прогона,\nа не подогнаны после",
    size=32, bold=True, line=1.02, space=0)

rows = [
    ("Точность по требованиям", "от 75%"),
    ("Ошибка по сумме баллов", "до 1 из 6"),
    ("Решение о зачёте совпало", "100%"),
    ("Вердиктов принято без правок", "от 60%"),
    ("Время на работу у ревьюера", "−30%"),
]
ty = Inches(2.75)
c1, c2, c3 = M, M + Inches(5.6), M + Inches(7.7)
c4 = M + Inches(9.6)
for lbl, x in (("что меряем", c1), ("порог", c2), ("результат", c3), ("откуда", c4)):
    box(s, x, ty, Inches(2.4), Inches(0.28), lbl, size=9.5, color=INK3, caps=True, space=0)
rect(s, M, ty + Inches(0.34), CW, Inches(0.012), fill=INK3)
for i, (name, thr) in enumerate(rows):
    y = ty + Inches(0.52) + Inches(0.62) * i
    box(s, c1, y, Inches(5.4), Inches(0.34), name, size=14, color=INK, space=0)
    box(s, c2, y, Inches(2.0), Inches(0.34), thr, size=14, color=INK, space=0)
    box(s, c3, y, Inches(1.6), Inches(0.34), "—", size=14, color=INK3, space=0)
    pill(s, c4, y - Inches(0.05), "цель", fill=WHITE, color=ORANGE, size=10)
    rect(s, M, y + Inches(0.44), CW, Inches(0.008), fill=LINE)

by = Inches(6.15)
bw = (CW - Inches(0.35)) / 2
rect(s, M, by, bw, Inches(0.95), fill=INK)
box(s, M + Inches(0.28), by + Inches(0.18), bw - Inches(0.56), Inches(0.28),
    "порог провала объявлен заранее", size=9.5, color=GREEN, caps=True, space=0)
box(s, M + Inches(0.28), by + Inches(0.48), bw - Inches(0.56), Inches(0.4),
    "Точность ниже 60% или принято меньше 40% пунктов. Тогда система перестаёт ставить "
    "вердикты и собирает выжимку с цитатами.", size=11, color=INK4, line=1.25)
rect(s, M + bw + Inches(0.35), by, bw, Inches(0.95), fill=None, line_col=LINE)
box(s, M + bw + Inches(0.63), by + Inches(0.18), bw - Inches(0.56), Inches(0.28),
    "что сравниваем", size=9.5, color=INK3, caps=True, space=0)
box(s, M + bw + Inches(0.63), by + Inches(0.48), bw - Inches(0.56), Inches(0.4),
    "Те же работы прогоняем одним общим промптом. Разница с нашим пайплайном и есть ответ, "
    "зачем это, если есть чат с моделью.", size=11, color=INK2, line=1.25)

foot(s, "Разметка ручная, по требованиям, набор работ зафиксирован в репозитории", "06")
s.notes_slide.notes_text_frame.text = (
    "ПЛЕЙСХОЛДЕР: колонка «результат» пустая, бенчмарк ещё не прогнан. "
    "Говорить так: пороги мы объявили до первого прогона и записали в репозиторий, включая "
    "порог провала. Сейчас в колонке результатов прочерки, прогон идёт. Что уже проверено — "
    "это разбор с цитатами, вы его видели в демо. Обещать здесь цифру, которой нет, я не буду."
)

# ========================= 07. Готовность и финал ===========================
s = slide()
kicker(s, "Готовность")
box(s, M, Inches(1.0), Inches(10.5), Inches(1.3),
    "Что работает, что измеряем\nи о чём просим", size=32, bold=True, line=1.02, space=0)

cols = [
    ("сделано", S2, INK, INK2, [
        "Разбор по требованиям с цитатами",
        "Общий пул и взятие работы",
        "Самопроверка студента до сдачи",
        "Журнал решений по каждой работе"]),
    ("проверяем сейчас", S2, INK, INK2, [
        "Точность против ручной разметки",
        "Сравнение с общим промптом",
        "Время ревьюера на шести работах"]),
    ("просим", GREEN, INK, INK, [
        "Пилот на одном потоке",
        "Доступ к критериям одного курса",
        "Два ревьюера на замер времени"]),
]
cy2, ch2 = Inches(2.75), Inches(2.35)
for i, (label, bg, lc, tc, items) in enumerate(cols):
    x = M + (cw + gap) * i
    rect(s, x, cy2, cw, ch2, fill=bg)
    box(s, x + Inches(0.28), cy2 + Inches(0.24), cw - Inches(0.56), Inches(0.28), label,
        size=9.5, color=INK3 if bg is S2 else INK, caps=True, space=0)
    for j, it in enumerate(items):
        yy = cy2 + Inches(0.62) + Inches(0.42) * j
        rect(s, x + Inches(0.28), yy + Inches(0.11), Inches(0.14), Inches(0.03), fill=GREEN if bg is S2 else INK)
        box(s, x + Inches(0.52), yy, cw - Inches(0.82), Inches(0.4), it, size=11.5, color=tc, line=1.25)

fy = Inches(5.4)
rect(s, M, fy, CW, Inches(1.5), fill=INK)
box(s, M + Inches(0.35), fy + Inches(0.2), CW - Inches(0.7), Inches(0.28),
    "финальная фраза, 12 секунд", size=9.5, color=GREEN, caps=True, space=0)
box(s, M + Inches(0.35), fy + Inches(0.55), CW - Inches(0.7), Inches(0.85),
    "«Ускорение проверки на курсе уже есть, но держится на одном человеке. Мы сделали его "
    "воспроизводимым: каждый вывод с цитатой, решение за ревьюером, журнал на каждую правку. "
    "Просим пилот на одном потоке, чтобы измерить эффект на реальных работах»",
    size=15, color=WHITE, line=1.3)
foot(s, "Avito AI Reviewer", "07")
s.notes_slide.notes_text_frame.text = (
    "Финальную фразу читаем дословно, не импровизируем. После неё молчим и ждём вопросов."
)

prs.save("docs/defense/avito-ai-reviewer-defense.pptx")
print("готово: docs/defense/avito-ai-reviewer-defense.pptx, слайдов:", len(prs.slides.__iter__.__self__._sldIdLst))
