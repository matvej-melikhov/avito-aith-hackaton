"""Скачивание снимка работы по подписанной ссылке с проверкой размера и sha256."""

from __future__ import annotations

import hashlib

import httpx


class ArtifactError(RuntimeError):
    def __init__(self, message: str, code: str = "invalid_artifact"):
        super().__init__(message)
        self.code = code


def digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def verify_digest(data: bytes, expected: str) -> None:
    if not expected:
        return
    actual = digest_of(data)
    if actual.lower() != expected.lower():
        raise ArtifactError(f"digest не совпал: ожидали {expected[:20]}…, получили {actual[:20]}…")


def rewrite_url(url: str, rewrites: str) -> tuple[str, dict[str, str]]:
    """Подменяет префикс адреса по правилам "from=to,from=to", сохраняя исходный Host в заголовке."""
    from urllib.parse import urlsplit

    for rule in [r.strip() for r in (rewrites or "").split(",") if r.strip()]:
        if "=" not in rule:
            continue
        src, dst = rule.split("=", 1)
        if url.startswith(src):
            original_host = urlsplit(url).netloc
            return dst + url[len(src):], {"Host": original_host}
    return url, {}


def fetch_artifact(url: str, expected_digest: str, *, max_bytes: int, timeout: int = 60,
                   rewrites: str = "") -> bytes:
    if url.startswith("file://"):
        # Локальные прогоны и тесты.
        from pathlib import Path

        data = Path(url[len("file://"):]).read_bytes()
    else:
        url, headers = rewrite_url(url, rewrites)
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                with client.stream("GET", url, headers=headers) as resp:
                    if resp.status_code != 200:
                        raise ArtifactError(f"ссылка на снимок вернула HTTP {resp.status_code}")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ArtifactError(f"снимок больше {max_bytes} байт")
                        chunks.append(chunk)
            data = b"".join(chunks)
        except httpx.HTTPError as e:
            raise ArtifactError(f"снимок не скачался: {type(e).__name__}") from e
    if len(data) > max_bytes:
        raise ArtifactError(f"снимок больше {max_bytes} байт")
    verify_digest(data, expected_digest)
    return data
