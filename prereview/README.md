# prereview: ядро ИИ-проверки домашних работ

Внешний AI-сервис платформы ревью. Проверяет работу по каждому критерию задания и отдаёт ревьюеру предложенный балл, объяснение, цитату с адресом, сигнал об ИИ и черновик фидбека. Итоговое решение за человеком.

Как устроено и как запускать: [docs/AI_CORE.md](../docs/AI_CORE.md). Результаты на публичном корпусе: [docs/EVALS.md](../docs/EVALS.md). Контракт с платформой: [specs/005-workspace-completion/ai-handoff.md](../specs/005-workspace-completion/ai-handoff.md).

## Быстрый старт

```bash
uv sync --directory prereview
uv run --directory prereview prereview grade "hw_examples/system_design/лаба 1/хорошее.md" -a system_design_lab1
uv run --directory prereview pytest
```

Ключ модели: `DEEPSEEK_API_KEY` в `.env` в корне репозитория. Модель и провайдер меняются переменными `PREREVIEW_LLM_MODEL`, `PREREVIEW_LLM_BASE_URL`, `PREREVIEW_LLM_PROVIDER` (`deepseek` или `fake`).

## Где что лежит

| Каталог | Что |
|---|---|
| `src/prereview/artifact` | скачивание снимка, извлечение md/docx/pdf/zip, усадка, опись, редактирование ПДн и инъекций |
| `src/prereview/rubric` | критерии платформы + файл задания = рубрика с классами проверок |
| `src/prereview/checks` | формальные примитивы и их прогон, сборка Go, клиент песочницы (`runtime.py`) |
| `runner/` | песочница запуска: отдельный образ без ключей, гоняет собранный бинарник по сценариям задания, goose и migrate для миграций |
| `src/prereview/judge` | судья по критерию, проверка цитат, самопроверка, исследователь репозиториев (DeepSeek Harness) |
| `src/prereview/signal` | функции-сигналы об ИИ и сводка |
| `src/prereview/service` | HTTP-сервис по контракту и sqlite-хранилище |
| `src/prereview/pipeline.py` | оркестратор прогона и журнал |
| `assignments/` | файлы заданий: классы критериев, формальные проверки, подсказки |
| `harness/` | патч конфигурации DeepSeek Harness (только чтение) |
| `evals/` | результаты прогонов корпуса и разметка |
| `../prompts/` | промпты, версия = хэш файла |
