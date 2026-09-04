"""Executable Taskiq email-worker entrypoint."""

from __future__ import annotations

from collections.abc import Sequence

from taskiq.cli.worker.cmd import WorkerCMD


def main(argv: Sequence[str] | None = None) -> int:
    arguments = [
        "review_platform.infrastructure.tasks.broker:get_email_broker",
        "--workers",
        "1",
        "--ack-type",
        "when_saved",
    ]
    if argv is not None:
        arguments.extend(argv)
    return WorkerCMD().exec(arguments) or 0


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
