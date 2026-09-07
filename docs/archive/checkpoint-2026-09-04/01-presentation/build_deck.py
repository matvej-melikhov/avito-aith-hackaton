#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Avito AI Reviewer — промежуточная презентация, чекпоинт 4 сентября.
Минималистичный стиль, python-pptx (node недоступен на машине).
"""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.oxml.ns import qn
import copy

# ---------- палитра ----------
NAVY      = RGBColor(0x1B, 0x2A, 0x4A)   # основной, доминирующий
NAVY_D    = RGBColor(0x12, 0x1D, 0x35)   # тёмный вариант для теней/акцентов на тёмном
OFFWHITE  = RGBColor(0xF5, 0xF7, 0xFA)   # светлый фон контентных слайдов
WHITE     = RGBColor(0xFF, 0xFF, 0xFF)
TEAL      = RGBColor(0x2E, 0x8B, 0x7A)   # единственный яркий акцент
TEAL_SOFT = RGBColor(0xE4, 0xF0, 0xEC)   # мягкая заливка под акцент
MUTED     = RGBColor(0x6B, 0x76, 0x8A)   # второстепенный текст
LINE      = RGBColor(0xDE, 0xE3, 0xEA)   # тонкие разделители / рамки карточек
WARN      = RGBColor(0xB4, 0x5A, 0x2E)   # предупреждение (риски), не ярко-красный
CARD_BG   = RGBColor(0xFF, 0xFF, 0xFF)

FONT_HEAD = "Cambria"
FONT_BODY = "Calibri"

SW, SH = Inches(13.333), Inches(7.5)

prs = Presentation()
prs.slide_width = SW
prs.slide_height = SH
BLANK = prs.slide_layouts[6]


def add_slide(bg=OFFWHITE):
    s = prs.slides.add_slide(BLANK)
    r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, SW, SH)
    r.fill.solid(); r.fill.fore_color.rgb = bg
    r.line.fill.background()
    r.shadow.inherit = False
    # отправить фон вниз z-order
    sp = r._element
    sp.getparent().remove(sp)
    s.shapes._spTree.insert(2, sp)
    return s


def _set_margins(tf, l=0, t=0, r=0, b=0):
    tf.margin_left = l; tf.margin_top = t; tf.margin_right = r; tf.margin_bottom = b


def textbox(slide, x, y, w, h, text, size=14, color=NAVY, bold=False, font=FONT_BODY,
            align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, italic=False, line_spacing=1.0,
            space_after=0, wrap=True):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = wrap
    _set_margins(tf)
    tf.vertical_anchor = anchor
    lines = text.split("\n")
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line_spacing
        p.space_after = Pt(space_after)
        run = p.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.italic = italic
        run.font.name = font
        run.font.color.rgb = color
    return tb


def bullets(slide, x, y, w, h, items, size=13.5, color=NAVY, font=FONT_BODY,
            space_after=8, line_spacing=1.08, marker="—", marker_color=None):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    _set_margins(tf)
    mc = marker_color or TEAL
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(space_after)
        p.line_spacing = line_spacing
        r1 = p.add_run(); r1.text = f"{marker}  "
        r1.font.size = Pt(size); r1.font.name = font; r1.font.color.rgb = mc; r1.font.bold = True
        r2 = p.add_run(); r2.text = item
        r2.font.size = Pt(size); r2.font.name = font; r2.font.color.rgb = color
    return tb


def rect(slide, x, y, w, h, fill=None, line_color=None, line_w=0.75, radius=None, shadow=False):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    sh = slide.shapes.add_shape(shape_type, x, y, w, h)
    if radius:
        try:
            sh.adjustments[0] = radius
        except Exception:
            pass
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid(); sh.fill.fore_color.rgb = fill
    if line_color is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line_color
        sh.line.width = Pt(line_w)
    sh.shadow.inherit = False
    if shadow:
        el = sh._element.spPr
        effect = el.makeelement(qn('a:effectLst'), {})
        outer = el.makeelement(qn('a:outerShdw'), {
            'blurRad': '90000', 'dist': '30000', 'dir': '5400000', 'rotWithShape': '0'})
        clr = el.makeelement(qn('a:srgbClr'), {'val': '1B2A4A'})
        alpha = el.makeelement(qn('a:alpha'), {'val': '18000'})
        clr.append(alpha)
        outer.append(clr)
        effect.append(outer)
        el.append(effect)
    return sh


def circle(slide, cx, cy, d, fill=TEAL, text=None, text_color=WHITE, size=16, bold=True, font=FONT_HEAD,
           line_color=None, line_w=1.0):
    sh = slide.shapes.add_shape(MSO_SHAPE.OVAL, cx, cy, d, d)
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid(); sh.fill.fore_color.rgb = fill
    if line_color is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line_color
        sh.line.width = Pt(line_w)
    sh.shadow.inherit = False
    if text is not None:
        tf = sh.text_frame
        _set_margins(tf)
        tf.word_wrap = False
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run(); r.text = text
        r.font.size = Pt(size); r.font.bold = bold; r.font.name = font
        r.font.color.rgb = text_color
    return sh


def connector(slide, x1, y1, x2, y2, color=LINE, w=1.0, dash=None, arrow=False):
    cn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x1, y1, x2, y2)
    cn.line.color.rgb = color
    cn.line.width = Pt(w)
    cn.shadow.inherit = False
    if dash:
        ln = cn.line._get_or_add_ln()
        d = ln.makeelement(qn('a:prstDash'), {'val': dash})
        ln.append(d)
    if arrow:
        ln = cn.line._get_or_add_ln()
        tail = ln.makeelement(qn('a:tailEnd'), {'type': 'triangle', 'w': 'med', 'len': 'med'})
        ln.append(tail)
    return cn


def kicker(slide, text, x=Inches(0.7), y=Inches(0.5), color=TEAL):
    textbox(slide, x, y, Inches(8), Inches(0.35), text.upper(), size=11, color=color,
            bold=True, font=FONT_BODY)
    # letter-spacing через charSpacing на run
    return


def page_number(slide, n, total, dark=False):
    c = WHITE if dark else MUTED
    textbox(slide, SW - Inches(1.1), SH - Inches(0.5), Inches(0.7), Inches(0.3),
            f"{n:02d} / {total:02d}", size=9.5, color=c, font=FONT_BODY, align=PP_ALIGN.RIGHT)


def footer_brand(slide, dark=False, text="AVITO AI REVIEWER"):
    c = RGBColor(0xB9, 0xC3, 0xD6) if dark else MUTED
    textbox(slide, Inches(0.7), SH - Inches(0.5), Inches(4), Inches(0.3),
            text, size=9.5, color=c, font=FONT_BODY, bold=True)


TOTAL = 13

# ============================================================ 1. TITLE
s = add_slide(bg=NAVY)
# тонкая декоративная сетка кругов справа — мотив "проверка/подтверждение"
RING = RGBColor(0x35, 0x47, 0x6E)
circle(s, Inches(10.9), Inches(1.0), Inches(2.6), fill=NAVY_D)
circle(s, Inches(11.5), Inches(3.6), Inches(1.5), fill=None, line_color=RING, line_w=1.25)
circle(s, Inches(9.7), Inches(4.6), Inches(0.9), fill=None, line_color=RING, line_w=1.25)
circle(s, Inches(11.65), Inches(4.25), Inches(0.42), fill=TEAL, text="✓", text_color=WHITE, size=17)

textbox(s, Inches(0.9), Inches(0.9), Inches(6), Inches(0.4), "AVITO AI REVIEWER", size=12.5,
        color=TEAL, bold=True, font=FONT_BODY)
textbox(s, Inches(0.9), Inches(2.5), Inches(9.5), Inches(2.2),
        "Промежуточный чекпоинт", size=44, color=WHITE, bold=True, font=FONT_HEAD, line_spacing=1.0)
textbox(s, Inches(0.9), Inches(3.55), Inches(9.5), Inches(0.9),
        "Движок проверки домашних работ по требованиям, с человеком в контуре решения",
        size=17, color=RGBColor(0xC7,0xCF,0xDD), font=FONT_BODY, line_spacing=1.15)
textbox(s, Inches(0.9), Inches(6.35), Inches(9), Inches(0.4),
        "4 сентября 2026, AI Talent Hub, хакатон AI Product Hack", size=12.5,
        color=RGBColor(0x9A, 0xA6, 0xBB), font=FONT_BODY)
textbox(s, Inches(0.9), Inches(6.72), Inches(9), Inches(0.4),
        "Мелихов Матвей — AI Product,  Губин Владимир — AI Engineer,  Поддуба Илья — AI Engineer",
        size=11.5, color=RGBColor(0x8B,0x97,0xAD), font=FONT_BODY)

# ============================================================ 2. ПРОБЛЕМА
s = add_slide()
kicker(s, "Проблема")
textbox(s, Inches(0.7), Inches(0.95), Inches(11.5), Inches(0.9),
        "У проверки нет единого состояния", size=32, color=NAVY, bold=True, font=FONT_HEAD)

rect(s, Inches(0.7), Inches(2.0), Inches(11.9), Inches(1.25), fill=TEAL_SOFT, radius=0.12)
textbox(s, Inches(1.05), Inches(2.18), Inches(11.2), Inches(0.95),
        "«Подробная обратная связь остаётся в GitHub или Stepik, а сводная информация "
        "хранится отдельно в Google Sheets. Из-за этого данные приходится сверять между "
        "несколькими системами, а при ручном переносе могут возникать ошибки или задержки.»",
        size=14.5, color=NAVY, italic=True, font=FONT_BODY, line_spacing=1.2)
textbox(s, Inches(1.05), Inches(2.95), Inches(6), Inches(0.3),
        "— из описания кейса, слова заказчика", size=11, color=MUTED, font=FONT_BODY)

cards = [
    ("Координация", "Методист вручную распределяет работы, следит за сроками, переносит\nрезультаты между системами.", "до 10 ч/нед"),
    ("Само ревью", "Ревьюер каждый раз заново держит в голове 5–9 критериев и вычитывает\nработу целиком, чтобы их сопоставить.", "5–10 ч/нед"),
    ("Ввод ревьюеров в строй", "Доступы внешним экспертам, калибровочные встречи, вычитка обратной\nсвязи новичков до отправки студенту.", "каждый поток заново"),
]
cx = Inches(0.7); cw = Inches(3.83); gap = Inches(0.2); cy = Inches(3.75); ch = Inches(2.85)
for i, (title, body, stat) in enumerate(cards):
    x = cx + i * (cw + gap)
    rect(s, x, cy, cw, ch, fill=CARD_BG, line_color=LINE, line_w=0.75, radius=0.06, shadow=True)
    circle(s, x + Inches(0.28), cy + Inches(0.28), Inches(0.42), fill=NAVY, text=str(i+1), size=14)
    textbox(s, x + Inches(0.28), cy + Inches(0.9), cw - Inches(0.56), Inches(0.45),
            title, size=16.5, color=NAVY, bold=True, font=FONT_HEAD)
    textbox(s, x + Inches(0.28), cy + Inches(1.4), cw - Inches(0.56), Inches(1.05),
            body, size=12, color=MUTED, font=FONT_BODY, line_spacing=1.2)
    textbox(s, x + Inches(0.28), cy + Inches(2.42), cw - Inches(0.56), Inches(0.35),
            stat, size=15, color=TEAL, bold=True, font=FONT_HEAD)

footer_brand(s); page_number(s, 2, TOTAL)

# ============================================================ 3. ПОСТАНОВКА ЗАДАЧИ
s = add_slide()
kicker(s, "Постановка задачи")
textbox(s, Inches(0.7), Inches(0.95), Inches(11.5), Inches(0.9),
        "Что требует кейс", size=32, color=NAVY, bold=True, font=FONT_HEAD)

textbox(s, Inches(0.7), Inches(1.85), Inches(5.6), Inches(0.4),
        "ТРИ ОБЯЗАТЕЛЬНЫЕ ФУНКЦИИ", size=12.5, color=TEAL, bold=True, font=FONT_BODY)
oblig = [
    "Распределение работ между ревьюерами с учётом нагрузки",
    "Предварительное ревью по критериям с рекомендациями",
    "Сигнал об использовании ИИ: уверенность и основания",
]
bullets(s, Inches(0.7), Inches(2.3), Inches(5.6), Inches(2.2), oblig, size=14.5, space_after=14)

textbox(s, Inches(6.85), Inches(1.85), Inches(5.7), Inches(0.4),
        "ПЛЮС МИНИМУМ 3 ДОПОЛНИТЕЛЬНЫЕ", size=12.5, color=TEAL, bold=True, font=FONT_BODY)
extra = [
    "Дедлайны, уведомления, расчёт штрафа",
    "Автозапись результата в Google Sheets",
    "Сравнение версий при повторном ревью",
    "Типичные ошибки потока — предложения по материалам",
]
bullets(s, Inches(6.85), Inches(2.3), Inches(5.7), Inches(2.4), extra, size=14.5, space_after=11)

rect(s, Inches(0.7), Inches(4.85), Inches(11.9), Inches(1.75), fill=NAVY, radius=0.06)
textbox(s, Inches(1.05), Inches(5.05), Inches(11.2), Inches(0.4),
        "ГЛАВНОЕ ОГРАНИЧЕНИЕ", size=11.5, color=TEAL, bold=True, font=FONT_BODY)
textbox(s, Inches(1.05), Inches(5.42), Inches(11.2), Inches(1.05),
        "Человек принимает финальное решение. ИИ не ставит оценку и не применяет санкции. "
        "Сигнал об ИИ — не доказательство: ревьюер подтверждает его или отклоняет.",
        size=16.5, color=WHITE, font=FONT_BODY, line_spacing=1.25)

footer_brand(s); page_number(s, 3, TOTAL)

# ============================================================ 4. НАШ ПОДХОД
s = add_slide()
kicker(s, "Наш подход")
textbox(s, Inches(0.7), Inches(0.95), Inches(11.9), Inches(0.9),
        "Один движок — LLM с тулами, три уровня строгости сигнала", size=28, color=NAVY, bold=True, font=FONT_HEAD)
textbox(s, Inches(0.7), Inches(1.72), Inches(11.3), Inches(0.5),
        "Любое требование детектит LLM, с тулом или без. Любой вердикт — сигнал, аппрувит ревьюер",
        size=13.5, color=MUTED, font=FONT_BODY)

classes = [
    ("Формальные", "LLM + тул", "«Построена диаграмма C4» — LLM вызывает тул поиска блока. Детерминированный факт, но тоже сигнал.", TEAL),
    ("Содержательные", "LLM + цитата", "«Есть ли декомпозиция по двум признакам» — LLM находит и цитирует, код валидирует цитату.", NAVY),
    ("Оценочные", "LLM советует", "«Обосновано ли SRP» — LLM предлагает интервал и аргументы, решает человек.", WARN),
]
cx = Inches(0.7); cw = Inches(3.83); gap = Inches(0.2); cy = Inches(2.45); ch = Inches(3.05)
for i, (title, tag, body, accent) in enumerate(classes):
    x = cx + i * (cw + gap)
    rect(s, x, cy, cw, ch, fill=CARD_BG, line_color=LINE, line_w=0.75, radius=0.06, shadow=True)
    rect(s, x, cy, cw, Inches(0.08), fill=accent)
    textbox(s, x + Inches(0.28), cy + Inches(0.32), cw - Inches(0.56), Inches(0.45),
            title, size=18, color=NAVY, bold=True, font=FONT_HEAD)
    textbox(s, x + Inches(0.28), cy + Inches(0.82), cw - Inches(0.56), Inches(0.35),
            tag.upper(), size=11, color=accent, bold=True, font=FONT_BODY)
    textbox(s, x + Inches(0.28), cy + Inches(1.28), cw - Inches(0.56), Inches(1.6),
            body, size=12.5, color=MUTED, font=FONT_BODY, line_spacing=1.25)

rect(s, Inches(0.7), Inches(5.75), Inches(11.9), Inches(0.95), fill=TEAL_SOFT, radius=0.1)
textbox(s, Inches(1.05), Inches(5.95), Inches(11.2), Inches(0.6),
        "На реальной рубрике формальные и содержательные сигналы уже дают 4.5 из 6 баллов "
        "высокой уверенности — все сигналы аппрувит ревьюер.",
        size=13.5, color=NAVY, bold=True, font=FONT_BODY, line_spacing=1.15)

footer_brand(s); page_number(s, 4, TOTAL)

# ============================================================ 5. АРХИТЕКТУРА
s = add_slide()
kicker(s, "Решение")
textbox(s, Inches(0.7), Inches(0.95), Inches(11.9), Inches(0.9),
        "Ядро не меняется, меняются адаптеры", size=30, color=NAVY, bold=True, font=FONT_HEAD)

# --- адаптеры входа (слева) ---
ax, ay, aw, ah = Inches(0.7), Inches(2.35), Inches(2.55), Inches(3.5)
rect(s, ax, ay, aw, ah, fill=CARD_BG, line_color=LINE, radius=0.08, shadow=True)
textbox(s, ax + Inches(0.22), ay + Inches(0.2), aw - Inches(0.44), Inches(0.3),
        "АДАПТЕРЫ ВХОДА", size=10.5, color=MUTED, bold=True, font=FONT_BODY)
for i, t in enumerate(["GitHub", "Stepik", "Google Docs"]):
    yy = ay + Inches(0.72) + i * Inches(0.85)
    rect(s, ax + Inches(0.22), yy, aw - Inches(0.44), Inches(0.6), fill=OFFWHITE, radius=0.15)
    textbox(s, ax + Inches(0.22), yy, aw - Inches(0.44), Inches(0.6), t, size=13, color=NAVY,
            font=FONT_BODY, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

# --- ядро (центр) ---
cx2, cy2, cw2, ch2 = Inches(3.85), Inches(2.35), Inches(4.55), Inches(3.5)
rect(s, cx2, cy2, cw2, ch2, fill=NAVY, radius=0.08, shadow=True)
textbox(s, cx2 + Inches(0.3), cy2 + Inches(0.22), cw2 - Inches(0.6), Inches(0.3),
        "ЯДРО", size=10.5, color=TEAL, bold=True, font=FONT_BODY)
steps = ["Разбор артефакта", "LLM по требованию (тул — опционально)", "Валидация цитат / тулов", "Сборка сигналов"]
for i, t in enumerate(steps):
    yy = cy2 + Inches(0.68) + i * Inches(0.68)
    circle(s, cx2 + Inches(0.3), yy, Inches(0.36), fill=TEAL, text=str(i+1), size=12)
    textbox(s, cx2 + Inches(0.8), yy - Inches(0.04), cw2 - Inches(1.1), Inches(0.45),
            t, size=13, color=WHITE, font=FONT_BODY, anchor=MSO_ANCHOR.MIDDLE)
textbox(s, cx2 + Inches(0.3), cy2 + Inches(3.02), cw2 - Inches(0.6), Inches(0.35),
        "все вердикты — сигналы, тул — опция LLM", size=10.5,
        color=RGBColor(0xB9,0xC3,0xD6), italic=True, font=FONT_BODY)

# --- человек (справа) ---
hx, hy, hw, hh = Inches(8.95), Inches(2.35), Inches(1.55), Inches(1.55)
circle(s, hx, hy, hw, fill=TEAL_SOFT, line_color=TEAL, line_w=1.25)
textbox(s, hx, hy + Inches(0.42), hw, Inches(0.6), "ЧЕЛОВЕК", size=12, color=NAVY, bold=True,
        font=FONT_BODY, align=PP_ALIGN.CENTER)
textbox(s, Inches(8.6), Inches(4.1), Inches(2.25), Inches(1.0),
        "Подтверждает, меняет\nили отклоняет. Обойти\nнельзя ни при каком\nвыходе.", size=11.5,
        color=MUTED, font=FONT_BODY, align=PP_ALIGN.CENTER, line_spacing=1.15)

# --- координация (справа снизу, отдельный модуль) ---
rx, ry, rw, rh = Inches(8.6), Inches(5.35), Inches(3.6), Inches(0.85)
rect(s, rx, ry, rw, rh, fill=OFFWHITE, line_color=LINE, radius=0.15)
textbox(s, rx + Inches(0.22), ry + Inches(0.1), rw - Inches(0.44), Inches(0.3),
        "КООРДИНАЦИЯ, ОТКЛЮЧАЕМЫЙ МОДУЛЬ", size=9.5, color=TEAL, bold=True, font=FONT_BODY)
textbox(s, rx + Inches(0.22), ry + Inches(0.4), rw - Inches(0.44), Inches(0.4),
        "Пул, сроки, штрафы — не нужен для ревью кода в CI", size=11, color=MUTED, font=FONT_BODY)

connector(s, ax + aw, ay + ah/2, cx2, cy2 + ch2/2, color=LINE, w=1.25, arrow=True)
connector(s, cx2 + cw2, cy2 + ch2/2, hx, hy + hh/2, color=LINE, w=1.25, arrow=True)
connector(s, hx + hw/2, hy + hh, hx + hw/2, ry, color=LINE, w=1.0, dash="dash")

footer_brand(s); page_number(s, 5, TOTAL)

# ============================================================ 6. PULL vs PUSH
s = add_slide()
kicker(s, "Новое решение недели")
textbox(s, Inches(0.7), Inches(0.95), Inches(11.9), Inches(0.9),
        "Координатор создаёт задание один раз, ревьюер тянет сам", size=27, color=NAVY, bold=True, font=FONT_HEAD)
textbox(s, Inches(0.7), Inches(1.68), Inches(11.3), Inches(0.5),
        "Главная боль координатора — не часы, а раздробленность работы во времени",
        size=13.5, color=MUTED, font=FONT_BODY)

was_items = [
    "Условие пишется где-то ещё — GitHub, Stepik, PDF",
    "Система пытается распознать критерии оттуда",
    "Координатор вычитывает и правит распознанное",
    "Координатор узнаёт у каждого ревьюера ёмкость",
    "Координатор раскладывает работы по ревьюерам",
]
now_items = [
    "Координатор один раз описывает задание в форме",
    "Сохранил — задание сразу видно ревьюерам",
    "Ревьюер сам указывает, сколько готов взять",
    "Система отдаёт ему работы из пула под ёмкость",
    "Координатор возвращается, только если что-то застряло",
]
colx = [Inches(0.7), Inches(6.85)]
titles = ["БЫЛО (черновик идеи)", "СТАЛО"]
itemsets = [was_items, now_items]
accents = [MUTED, TEAL]
for ci in range(2):
    x = colx[ci]
    rect(s, x, Inches(2.35), Inches(5.7), Inches(4.35), fill=CARD_BG, line_color=LINE, radius=0.06, shadow=True)
    textbox(s, x + Inches(0.32), Inches(2.6), Inches(5.1), Inches(0.35),
            titles[ci], size=12, color=accents[ci], bold=True, font=FONT_BODY)
    yy = Inches(3.15)
    for i, item in enumerate(itemsets[ci]):
        circle(s, x + Inches(0.32), yy, Inches(0.34), fill=accents[ci], text=str(i+1), size=11.5)
        textbox(s, x + Inches(0.82), yy - Inches(0.03), Inches(4.55), Inches(0.6),
                item, size=12.5, color=NAVY, font=FONT_BODY, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1.05)
        yy += Inches(0.72)

footer_brand(s); page_number(s, 6, TOTAL)

# ============================================================ 7. ЧТО СДЕЛАНО
s = add_slide()
kicker(s, "Прогресс")
textbox(s, Inches(0.7), Inches(0.95), Inches(11.9), Inches(0.9),
        "Что сделано ко второму сентября — четвёртому", size=30, color=NAVY, bold=True, font=FONT_HEAD)

steps7 = [
    ("Разбор кейса и материалов", "Описание кейса, драфт задачи, 8 направлений примеров работ из репозитория"),
    ("Интервью с реальным ревьюером", "Расшифровка встречи с ревьюером курса Tech QA — первичные данные вместо догадок"),
    ("Продуктовая постановка", "Проблема, цена ошибки, метрики, 10 гипотез, контракты — что нельзя нарушать"),
    ("Архитектура и модель данных", "Ядро с адаптерами, 16 сущностей с ER-диаграммой, три класса требований"),
    ("23 экрана в макете", "Студент, ревьюер, координатор — от регистрации до журнала решений"),
]
ty = Inches(2.1)
line_x = Inches(1.05)
connector(s, line_x, ty + Inches(0.2), line_x, ty + Inches(0.2) + Inches(0.98) * (len(steps7) - 1), color=LINE, w=1.5)
for i, (title, body) in enumerate(steps7):
    yy = ty + Inches(0.98) * i
    circle(s, line_x - Inches(0.19), yy, Inches(0.38), fill=TEAL, text="✓", size=13)
    textbox(s, line_x + Inches(0.45), yy - Inches(0.14), Inches(3.6), Inches(0.45),
            title, size=14.5, color=NAVY, bold=True, font=FONT_BODY, anchor=MSO_ANCHOR.MIDDLE)
    textbox(s, line_x + Inches(4.2), yy - Inches(0.14), Inches(7.4), Inches(0.5),
            body, size=12, color=MUTED, font=FONT_BODY, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1.1)

footer_brand(s); page_number(s, 7, TOTAL)

# ============================================================ 8. ПЕРВЫЕ ДАННЫЕ
s = add_slide()
kicker(s, "Первые данные")
textbox(s, Inches(0.7), Inches(0.95), Inches(11.9), Inches(0.9),
        "Не гипотеза — интервью с реальным ревьюером", size=30, color=NAVY, bold=True, font=FONT_HEAD)
textbox(s, Inches(0.7), Inches(1.72), Inches(11.3), Inches(0.5),
        "Ревьюер курса Tech QA рассказал, как проверка устроена сегодня — с их собственным навыком для модели",
        size=13, color=MUTED, font=FONT_BODY)

stats = [
    ("~1 час", "ручная проверка одной работы, первые два потока"),
    ("10–15 мин", "с их навыком для модели — ускорение в 4–6 раз"),
    ("~100", "студентов на типовой поток"),
    ("5–6", "ревьюеров минимум на один тип задания"),
]
cx = Inches(0.7); cw = Inches(2.85); gap = Inches(0.17); cy = Inches(2.45); ch = Inches(1.7)
for i, (num, cap) in enumerate(stats):
    x = cx + i * (cw + gap)
    rect(s, x, cy, cw, ch, fill=CARD_BG, line_color=LINE, radius=0.08, shadow=True)
    textbox(s, x + Inches(0.18), cy + Inches(0.28), cw - Inches(0.36), Inches(0.65),
            num, size=30, color=TEAL, bold=True, font=FONT_HEAD)
    textbox(s, x + Inches(0.18), cy + Inches(1.0), cw - Inches(0.36), Inches(0.6),
            cap, size=10.5, color=MUTED, font=FONT_BODY, line_spacing=1.1)

rect(s, Inches(0.7), Inches(4.5), Inches(11.9), Inches(2.15), fill=RGBColor(0xFB,0xEF,0xE6), radius=0.08)
textbox(s, Inches(1.05), Inches(4.72), Inches(11.2), Inches(0.35),
        "НАХОДКА, КОТОРАЯ МЕНЯЕТ НАШ ДИЗАЙН", size=11.5, color=WARN, bold=True, font=FONT_BODY)
textbox(s, Inches(1.05), Inches(5.1), Inches(11.2), Inches(1.4),
        "Критерии на курсе описывают намеренно неполно — ревьюер лично проверил: скормил хорошо "
        "структурированное условие модели как есть, сдал результат как студент, работа прошла проверку. "
        "Это прямой риск для нашей идеи саморевью, где студент видит требования один в один. Решение "
        "ещё не выбрано — фиксируем как открытый вопрос, не как готовый ответ.",
        size=13.5, color=NAVY, font=FONT_BODY, line_spacing=1.25)

footer_brand(s); page_number(s, 8, TOTAL)

# ============================================================ 9. МАКЕТЫ
s = add_slide()
kicker(s, "Проработка UX")
textbox(s, Inches(0.7), Inches(0.95), Inches(11.9), Inches(0.9),
        "23 экрана по трём ролям", size=30, color=NAVY, bold=True, font=FONT_HEAD)
textbox(s, Inches(0.7), Inches(1.72), Inches(5.6), Inches(0.9),
        "Каждый вывод модели — с цитатой из работы. Код проверяет, что цитата реальна, "
        "иначе вердикт отклоняется.", size=13.5, color=MUTED, font=FONT_BODY, line_spacing=1.25)

roles9 = [("Студент", "5 экранов", "сдача, саморевью, статус"),
          ("Ревьюер", "5 экранов", "кабинет, карточка работы, статистика"),
          ("Координатор", "13 экранов", "курсы, задания, пул, контроль")]
ry = Inches(2.85)
for i, (r, n, d) in enumerate(roles9):
    yy = ry + Inches(1.15) * i
    circle(s, Inches(0.7), yy, Inches(0.42), fill=NAVY, text=str(i+1), size=13)
    textbox(s, Inches(1.28), yy - Inches(0.05), Inches(2.0), Inches(0.4), r, size=15, color=NAVY,
            bold=True, font=FONT_BODY, anchor=MSO_ANCHOR.MIDDLE)
    textbox(s, Inches(1.28), yy + Inches(0.38), Inches(3.6), Inches(0.35), n + ", " + d, size=11.5,
            color=MUTED, font=FONT_BODY)

# мини-макет карточки ревью справа
mx, my, mw, mh = Inches(6.9), Inches(1.75), Inches(5.7), Inches(5.15)
rect(s, mx, my, mw, mh, fill=CARD_BG, line_color=LINE, line_w=0.75, radius=0.06, shadow=True)
rect(s, mx, my, mw, Inches(0.5), fill=OFFWHITE, radius=0.0)
textbox(s, mx + Inches(0.25), my, mw - Inches(0.5), Inches(0.5), "reviewer / карточка работы",
        size=10.5, color=MUTED, font=FONT_BODY, anchor=MSO_ANCHOR.MIDDLE)
textbox(s, mx + Inches(0.3), my + Inches(0.72), mw - Inches(0.6), Inches(0.4),
        "Студент 4821 — Wardrobe Service", size=15, color=NAVY, bold=True, font=FONT_BODY)
textbox(s, mx + mw - Inches(1.6), my + Inches(0.7), Inches(1.3), Inches(0.5),
        "4.5 из 6", size=17, color=TEAL, bold=True, font=FONT_HEAD, align=PP_ALIGN.RIGHT)

req_y = my + Inches(1.35)
for i, (name, verdict, quote, vcolor) in enumerate([
    ("Диаграмма C4, контекстная и компонентная", "выполнено", "«C4Context, C4Component» — оба блока найдены", TEAL),
    ("Обосновано соблюдение SRP", "нужно решение", "раздел есть, обоснование неполное", WARN),
]):
    yy = req_y + Inches(1.55) * i
    rect(s, mx + Inches(0.3), yy, mw - Inches(0.6), Inches(1.35), fill=OFFWHITE, radius=0.08)
    textbox(s, mx + Inches(0.5), yy + Inches(0.12), mw - Inches(1.9), Inches(0.4),
            name, size=12, color=NAVY, bold=True, font=FONT_BODY)
    textbox(s, mx + mw - Inches(2.05), yy + Inches(0.13), Inches(1.6), Inches(0.35),
            verdict, size=10, color=vcolor, bold=True, font=FONT_BODY, align=PP_ALIGN.RIGHT)
    rect(s, mx + Inches(0.5), yy + Inches(0.56), mw - Inches(1.0), Inches(0.62), fill=WHITE, line_color=TEAL, line_w=1.5, radius=0.1)
    textbox(s, mx + Inches(0.68), yy + Inches(0.56), mw - Inches(1.36), Inches(0.62),
            quote, size=10.5, color=MUTED, italic=True, font=FONT_BODY, anchor=MSO_ANCHOR.MIDDLE)

footer_brand(s); page_number(s, 9, TOTAL)

# ============================================================ 10. РИСКИ
s = add_slide()
kicker(s, "Честно о рисках")
textbox(s, Inches(0.7), Inches(0.95), Inches(11.9), Inches(0.9),
        "Что может не получиться", size=32, color=NAVY, bold=True, font=FONT_HEAD)

risks = [
    ("01", "Главная гипотеза ещё не проверена",
     "«Пре-ревью достаточно точное, чтобы ревьюер его принимал, а не переписывал» — по плану "
     "проверяем первой, ещё до кода. Прогон не сделан. Порог провала объявлен заранее: точность "
     "по критериям < 60% или ревьюер принимает < 40% пунктов — тогда пивот на выжимку без вердиктов.",
     True),
    ("02", "Точные критерии — уязвимость",
     "Если требования полностью структурированы и видны студенту в саморевью, работу можно "
     "скормить модели целиком. У Авито критерии поэтому намеренно неполные. Решение для нас пока "
     "не выбрано.", False),
    ("03", "Часть вопросов кейсодателю без ответа",
     "Контур для моделей, приемлемая стоимость проверки, реальные баллы по критериям для калибровки. "
     "Работаем на зафиксированных допущениях, меняем на факты по мере ответов.", False),
]
ry = Inches(2.05)
for i, (n, title, body, urgent) in enumerate(risks):
    yy = ry + Inches(1.72) * i
    accent = WARN if urgent else MUTED
    rect(s, Inches(0.7), yy, Inches(11.9), Inches(1.5), fill=CARD_BG, line_color=LINE, radius=0.06, shadow=True)
    textbox(s, Inches(1.0), yy + Inches(0.18), Inches(0.9), Inches(0.7), n, size=26, color=RGBColor(0xE3,0xE6,0xEC),
            bold=True, font=FONT_HEAD)
    textbox(s, Inches(1.95), yy + Inches(0.17), Inches(9.9), Inches(0.4),
            title, size=15.5, color=NAVY, bold=True, font=FONT_BODY)
    textbox(s, Inches(1.95), yy + Inches(0.58), Inches(9.9), Inches(0.85),
            body, size=11.5, color=MUTED, font=FONT_BODY, line_spacing=1.18)
    if urgent:
        rect(s, Inches(0.7), yy, Inches(0.08), Inches(1.5), fill=WARN)

footer_brand(s); page_number(s, 10, TOTAL)

# ============================================================ 11. ПЛАН
s = add_slide()
kicker(s, "План")
textbox(s, Inches(0.7), Inches(0.95), Inches(11.9), Inches(0.9),
        "До финальной защиты — 7 сентября", size=30, color=NAVY, bold=True, font=FONT_HEAD)

plan_cols = [
    ("Сегодня-завтра", ["Прогнать главную гипотезу на 3 работах", "Замерить время: 3 работы вручную, 3 с черновиком", "По итогу решить: продолжаем или пивот"]),
    ("До 6 сентября", ["Рабочий MVP: адаптер GitHub, проверки кодом", "Вызовы модели по требованиям с цитатами", "Автозапись в Google Sheets, дедлайны, штрафы"]),
    ("6-7 сентября", ["Второй профиль задания, если успеваем", "Заморозка кода утром 7 сентября", "Репетиции защиты, финальные слайды"]),
]
cx = Inches(0.7); cw = Inches(3.83); gap = Inches(0.2); cy = Inches(2.05); ch = Inches(4.5)
for i, (title, items) in enumerate(plan_cols):
    x = cx + i * (cw + gap)
    rect(s, x, cy, cw, ch, fill=CARD_BG, line_color=LINE, radius=0.06, shadow=True)
    rect(s, x, cy, cw, Inches(0.7), fill=NAVY, radius=0.06)
    rect(s, x, cy + Inches(0.35), cw, Inches(0.35), fill=NAVY)
    textbox(s, x + Inches(0.26), cy, cw - Inches(0.52), Inches(0.7), title, size=15, color=WHITE,
            bold=True, font=FONT_BODY, anchor=MSO_ANCHOR.MIDDLE)
    bullets(s, x + Inches(0.26), cy + Inches(0.95), cw - Inches(0.5), ch - Inches(1.2), items,
            size=12, space_after=14, line_spacing=1.15)

footer_brand(s); page_number(s, 11, TOTAL)

# ============================================================ 12. КОМАНДА
s = add_slide()
kicker(s, "Команда")
textbox(s, Inches(0.7), Inches(0.95), Inches(11.9), Inches(0.9),
        "Три человека, 2-7 сентября", size=32, color=NAVY, bold=True, font=FONT_HEAD)

team = [
    ("Мелихов Матвей", "AI Product",
     "Постановка и доказательства: критерии, разметка, метрики и гипотезы, связь с кейсодателем, "
     "сценарий демо, презентация. Право резать скоуп.",
     "Активно, весь период"),
    ("Губин Владимир", "AI Engineer",
     "Ядро проверки и интеграции: проверки кодом, вызовы модели по требованиям, сигнал об ИИ, "
     "сервис, GitHub и Google Sheets, интерфейс ревьюера.",
     "Активно, отдельная ветка"),
    ("Поддуба Илья", "AI Engineer",
     "Ядро проверки и интеграции: проверки кодом, вызовы модели по требованиям, сигнал об ИИ, "
     "сервис, GitHub и Google Sheets, интерфейс ревьюера.",
     "Активно, отдельная ветка"),
]
cx = Inches(0.7); cw = Inches(3.83); gap = Inches(0.2); cy = Inches(2.05); ch = Inches(4.5)
for i, (name, role, resp, status) in enumerate(team):
    x = cx + i * (cw + gap)
    rect(s, x, cy, cw, ch, fill=CARD_BG, line_color=LINE, radius=0.06, shadow=True)
    circle(s, x + Inches(0.28), cy + Inches(0.28), Inches(0.55), fill=NAVY,
           text=name.split()[1][0] + name.split()[0][0], size=15)
    textbox(s, x + Inches(0.28), cy + Inches(1.0), cw - Inches(0.56), Inches(0.4),
            name, size=16, color=NAVY, bold=True, font=FONT_HEAD)
    textbox(s, x + Inches(0.28), cy + Inches(1.4), cw - Inches(0.56), Inches(0.32),
            role.upper(), size=10.5, color=TEAL, bold=True, font=FONT_BODY)
    textbox(s, x + Inches(0.28), cy + Inches(1.82), cw - Inches(0.56), Inches(1.75),
            resp, size=11.5, color=MUTED, font=FONT_BODY, line_spacing=1.22)
    rect(s, x + Inches(0.28), cy + Inches(3.75), cw - Inches(0.56), Inches(0.5), fill=TEAL_SOFT, radius=0.2)
    textbox(s, x + Inches(0.28), cy + Inches(3.75), cw - Inches(0.56), Inches(0.5),
            status, size=10.5, color=TEAL, bold=True, font=FONT_BODY, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

footer_brand(s); page_number(s, 12, TOTAL)

# ============================================================ 13. CLOSING
s = add_slide(bg=NAVY)
circle(s, Inches(-1.0), Inches(-1.2), Inches(4.0), fill=NAVY_D)
circle(s, Inches(11.2), Inches(4.8), Inches(3.4), fill=NAVY_D)

textbox(s, Inches(0.9), Inches(2.55), Inches(10), Inches(1.2),
        "Открытые вопросы и следующий шаг", size=36, color=WHITE, bold=True, font=FONT_HEAD)
textbox(s, Inches(0.9), Inches(3.55), Inches(10.5), Inches(1.1),
        "Прогон главной гипотезы на реальных работах — уже сегодня. Он решает, движемся "
        "дальше в текущем виде или перестраиваем ядро.",
        size=16, color=RGBColor(0xC7,0xCF,0xDD), font=FONT_BODY, line_spacing=1.3)
textbox(s, Inches(0.9), Inches(5.4), Inches(9), Inches(0.4),
        "github.com/matvej-melikhov/avito-aith-hackaton, ветка matvej", size=12.5,
        color=TEAL, font=FONT_BODY)
textbox(s, Inches(0.9), Inches(6.6), Inches(9), Inches(0.4),
        "Спасибо", size=13, color=RGBColor(0x8B,0x97,0xAD), font=FONT_BODY)

prs.save("/tmp/_deck_part1.pptx")
print("part FINAL saved, slides:", len(prs.slides._sldIdLst))
