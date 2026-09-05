"""PDF без текстового слоя: страницы рендерятся в картинки и распознаются vision-моделью.

Результат помечается флагом ocr_transcribed: цитаты судьи относятся к распознанному тексту,
а не к исходному файлу, и ревьюер это видит. Любая ошибка распознавания возвращает None,
и работа остаётся «не проверено», а не получает выдуманный текст.
"""

from __future__ import annotations

import base64
import logging
import time

from prereview.artifact.model import Work, WorkFile
from prereview.config import Settings
from prereview.llm.ledger import Ledger, Usage

log = logging.getLogger(__name__)

SYSTEM = ("Ты распознаёшь текст со страницы учебной работы. Перепиши весь текст дословно, сохраняя порядок, "
          "заголовки, списки и таблицы (таблицы как markdown). Подписи к схемам и содержимое блоков схем тоже перепиши. "
          "Ничего не добавляй и не оценивай. Если текста на странице нет, ответь: (пустая страница).")


MAX_SIDE_PX = 3600  # провайдер отклоняет очень большие картинки; экспорт досок бывает 10000 px в ширину
MAX_TILES_PER_PAGE = 6


def render_pages(data: bytes, max_pages: int, dpi: int = 110) -> list[bytes]:
    """PNG-картинки страниц. Широкие страницы (экспорт досок) режутся на вертикальные полосы."""
    import pymupdf

    out: list[bytes] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            if len(out) >= max_pages:
                break
            zoom = dpi / 72
            w, h = page.rect.width * zoom, page.rect.height * zoom
            if h > MAX_SIDE_PX:
                zoom *= MAX_SIDE_PX / h
                w, h = page.rect.width * zoom, page.rect.height * zoom
            tiles = min(MAX_TILES_PER_PAGE, max(1, int(w // MAX_SIDE_PX) + (1 if w % MAX_SIDE_PX else 0)))
            if tiles > 1 and w / tiles > MAX_SIDE_PX:
                zoom *= MAX_SIDE_PX * tiles / w
            tile_w = page.rect.width / tiles
            for i in range(tiles):
                if len(out) >= max_pages:
                    break
                clip = pymupdf.Rect(page.rect.x0 + i * tile_w, page.rect.y0, page.rect.x0 + (i + 1) * tile_w, page.rect.y1)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
                out.append(pix.tobytes("png"))
    return out


def transcribe_pdf(data: bytes, settings: Settings, ledger: Ledger) -> tuple[str, int] | None:
    """Возвращает (текст, число страниц) или None при отказе."""
    if not settings.ocr_enabled or not settings.api_key:
        return None
    try:
        pages = render_pages(data, settings.ocr_max_pages)
    except Exception as e:  # noqa: BLE001
        log.warning("ocr render failed: %s", e)
        return None
    if not pages:
        return None
    from openai import OpenAI

    client = OpenAI(api_key=settings.api_key, base_url=settings.llm_base_url.rstrip("/") + "/v1",
                    timeout=settings.llm_timeout_seconds, max_retries=1)
    texts: list[str] = []
    started = time.time()
    total = Usage()
    for i, png in enumerate(pages, 1):
        b64 = base64.b64encode(png).decode("ascii")
        try:
            resp = client.chat.completions.create(
                model=settings.ocr_model,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text", "text": f"Страница {i} из {len(pages)}."},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]},
                ],
                max_tokens=3000,
                temperature=0,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("ocr page %d failed: %s", i, e)
            return None
        u = Usage.from_api(resp.usage)
        total = Usage(total.prompt_tokens + u.prompt_tokens, total.completion_tokens + u.completion_tokens,
                      total.cache_hit_tokens + u.cache_hit_tokens)
        text = (resp.choices[0].message.content or "").strip()
        if len(text) < 20:
            # Пустой ответ на непустой полосе бывает; один повтор с прямым вопросом.
            try:
                again = client.chat.completions.create(
                    model=settings.ocr_model, max_tokens=3000, temperature=0,
                    messages=[{"role": "system", "content": SYSTEM},
                              {"role": "user", "content": [
                                  {"type": "text", "text": "На этой картинке есть текст. Перепиши весь текст дословно."},
                                  {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}])
                text2 = (again.choices[0].message.content or "").strip()
                u2 = Usage.from_api(again.usage)
                total = Usage(total.prompt_tokens + u2.prompt_tokens, total.completion_tokens + u2.completion_tokens,
                              total.cache_hit_tokens + u2.cache_hit_tokens)
                if len(text2) > len(text):
                    text = text2
            except Exception as e:  # noqa: BLE001
                log.warning("ocr retry page %d failed: %s", i, e)
        texts.append(f"<!-- страница {i} -->\n" + text)
    ledger.record("ocr", settings.ocr_model, total, time.time() - started, len(pages), "vision")
    return "\n\n".join(texts), len(pages)


def apply_ocr(work: Work, data: bytes, settings: Settings, ledger: Ledger) -> Work:
    """Заменяет пустой текст PDF на распознанный; при отказе возвращает работу как есть."""
    if "needs_vision" not in work.flags:
        return work
    result = transcribe_pdf(data, settings, ledger)
    if result is None:
        return work
    text, pages = result
    if len(text.strip()) < 50:
        return work
    name = work.files[0].path if work.files else "work.pdf"
    new = Work(work.format, work.media_type, [WorkFile(name, text, "text")],
               [f for f in work.flags if f != "needs_vision"] + ["ocr_transcribed"],
               dict(work.meta, ocr_pages=pages, chars=len(text)))
    return new
