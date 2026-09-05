"""Модель за общим интерфейсом.

DeepSeekClient — любой OpenAI-совместимый API (DeepSeek, локальный vLLM, модель Авито).
FakeClient — детерминированная заглушка для тестов и офлайн-демо.

Структурированный выход: сначала strict tool call (валидация схемы на стороне провайдера),
при отказе — режим json_object с проверкой pydantic и повтором с текстом ошибки.
Любая ошибка после всех попыток — LLMError; вызывающий код превращает её в
«не проверено», никогда в балл.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ValidationError

from prereview.config import Settings
from prereview.llm.ledger import Ledger, Usage

log = logging.getLogger(__name__)

STRICT_DROP_KEYS = {
    "title", "default", "minLength", "maxLength", "minItems", "maxItems",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "pattern",
    "format", "examples", "multipleOf", "uniqueItems",
}


class LLMError(RuntimeError):
    """Модель не дала пригодного ответа после всех попыток."""


@dataclass
class LLMResult[T]:
    value: T
    usage: Usage
    model: str
    attempts: int
    elapsed: float
    path: str


def strict_schema(model: type[BaseModel]) -> dict:
    """JSON Schema под strict-режим DeepSeek: все поля required, без лишних ключей."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in STRICT_DROP_KEYS:
                continue
            if key in {"properties", "$defs"}:
                out[key] = {k: walk(v) for k, v in value.items()}
            else:
                out[key] = walk(value)
        if "properties" in out:
            out["required"] = list(out["properties"].keys())
            out["additionalProperties"] = False
        return out

    return walk(model.model_json_schema())


def _sum(a: Usage, b: Usage) -> Usage:
    return Usage(
        a.prompt_tokens + b.prompt_tokens,
        a.completion_tokens + b.completion_tokens,
        a.cache_hit_tokens + b.cache_hit_tokens,
    )


def extract_json(text: str) -> str:
    """Снимает ```json-ограждения и берёт первый объект верхнего уровня."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("в ответе нет JSON-объекта")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("JSON-объект не закрыт")


class LLMClient(Protocol):
    model: str

    def complete_structured[T: BaseModel](
        self, *, system: str, user: str, schema: type[T], tag: str,
        max_tokens: int = 2000, temperature: float = 0.0,
    ) -> LLMResult[T]: ...

    def complete_text(
        self, *, system: str, user: str, tag: str, max_tokens: int = 1500, temperature: float = 0.0
    ) -> LLMResult[str]: ...


class DeepSeekClient:
    def __init__(self, settings: Settings, ledger: Ledger):
        from openai import OpenAI

        if not settings.api_key:
            raise LLMError("DEEPSEEK_API_KEY не задан")
        base = settings.llm_base_url.rstrip("/")
        kwargs = dict(api_key=settings.api_key, timeout=settings.llm_timeout_seconds, max_retries=2)
        self.beta = OpenAI(base_url=f"{base}/beta", **kwargs)
        self.v1 = OpenAI(base_url=f"{base}/v1", **kwargs)
        self.model = settings.llm_model
        self.ledger = ledger

    # --- структурированный выход ---------------------------------------------

    def complete_structured[T: BaseModel](
        self, *, system: str, user: str, schema: type[T], tag: str,
        max_tokens: int = 2000, temperature: float = 0.0,
    ) -> LLMResult[T]:
        from openai import APIStatusError

        started = time.time()
        total = Usage()
        attempts = 0
        errors: list[str] = []
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        tool = {
            "type": "function",
            "function": {
                "name": "submit_result",
                "description": "Отдать результат строго по схеме.",
                "strict": True,
                "parameters": strict_schema(schema),
            },
        }
        for _ in range(2):
            attempts += 1
            try:
                resp = self.beta.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=[tool],
                    tool_choice={"type": "function", "function": {"name": "submit_result"}},
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                total = _sum(total, Usage.from_api(resp.usage))
                msg = resp.choices[0].message
                if not msg.tool_calls:
                    raise LLMError("модель не вызвала инструмент")
                value = schema.model_validate_json(msg.tool_calls[0].function.arguments)
                return self._done(tag, value, total, attempts, started, "strict_tool")
            except APIStatusError as e:
                errors.append(f"strict: HTTP {e.status_code}: {str(e)[:200]}")
                break  # схема не принята провайдером, идём в json_object
            except (ValidationError, LLMError, ValueError) as e:
                errors.append(f"strict: {str(e)[:200]}")
                continue

        example = schema.model_json_schema()
        hint = (
            "\n\nОтветь одним JSON-объектом без пояснений. JSON Schema ответа:\n"
            + json.dumps(example, ensure_ascii=False)
        )
        user_json = user + hint
        for _ in range(2):
            attempts += 1
            try:
                resp = self.v1.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system + " Отвечай только JSON."},
                        {"role": "user", "content": user_json},
                    ],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                total = _sum(total, Usage.from_api(resp.usage))
                content = resp.choices[0].message.content or ""
                value = schema.model_validate_json(extract_json(content))
                return self._done(tag, value, total, attempts, started, "json_object")
            except (ValidationError, ValueError) as e:
                errors.append(f"json: {str(e)[:300]}")
                user_json = user + hint + f"\n\nПредыдущий ответ отклонён: {str(e)[:500]}. Исправь."
            except Exception as e:  # noqa: BLE001 — сеть, провайдер
                errors.append(f"json: {type(e).__name__}: {str(e)[:200]}")
        self.ledger.record(tag, self.model, total, time.time() - started, attempts, "failed")
        raise LLMError("; ".join(errors))

    def complete_text(
        self, *, system: str, user: str, tag: str, max_tokens: int = 1500, temperature: float = 0.0
    ) -> LLMResult[str]:
        started = time.time()
        resp = self.v1.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body={"thinking": {"type": "disabled"}},
        )
        usage = Usage.from_api(resp.usage)
        content = resp.choices[0].message.content or ""
        return self._done(tag, content, usage, 1, started, "text")

    def _done[T](self, tag: str, value: T, usage: Usage, attempts: int, started: float, path: str) -> LLMResult[T]:
        elapsed = time.time() - started
        self.ledger.record(tag, self.model, usage, elapsed, attempts, path)
        return LLMResult(value, usage, self.model, attempts, elapsed, path)


Responder = Callable[[str, type[BaseModel], str, str], BaseModel | dict | str]


class FakeClient:
    """Заглушка: отвечает через responder(tag, schema, system, user) или schema.fake_example()."""

    model = "fake"

    def __init__(self, ledger: Ledger, responder: Responder | None = None):
        self.ledger = ledger
        self.responder = responder
        self.calls: list[dict] = []

    def complete_structured[T: BaseModel](
        self, *, system: str, user: str, schema: type[T], tag: str,
        max_tokens: int = 2000, temperature: float = 0.0,
    ) -> LLMResult[T]:
        self.calls.append({"tag": tag, "schema": schema.__name__, "system": system, "user": user})
        raw: Any
        if self.responder is not None:
            raw = self.responder(tag, schema, system, user)
        elif hasattr(schema, "fake_example"):
            raw = schema.fake_example()  # type: ignore[attr-defined]
        else:
            raise LLMError(f"FakeClient: нет ответа для {schema.__name__}")
        if isinstance(raw, LLMError):
            raise raw
        value = raw if isinstance(raw, schema) else schema.model_validate(raw)
        usage = Usage((len(system) + len(user)) // 4, 120, 0)
        self.ledger.record(tag, self.model, usage, 0.0, 1, "fake")
        return LLMResult(value, usage, self.model, 1, 0.0, "fake")

    def complete_text(
        self, *, system: str, user: str, tag: str, max_tokens: int = 1500, temperature: float = 0.0
    ) -> LLMResult[str]:
        self.calls.append({"tag": tag, "schema": "text", "system": system, "user": user})
        text = "Привет! Черновик заглушки.\n1. Проверь работу вручную."
        if self.responder is not None:
            out = self.responder(tag, BaseModel, system, user)
            text = out if isinstance(out, str) else text
        usage = Usage((len(system) + len(user)) // 4, 60, 0)
        self.ledger.record(tag, self.model, usage, 0.0, 1, "fake")
        return LLMResult(text, usage, self.model, 1, 0.0, "fake")


def make_client(settings: Settings, ledger: Ledger, responder: Responder | None = None) -> LLMClient:
    if settings.llm_provider == "fake":
        return FakeClient(ledger, responder)
    return DeepSeekClient(settings, ledger)
