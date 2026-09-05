"""Measured publication statistics; peer comparisons require identical immutable inputs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from review_platform.contracts.workspace import (
    CriterionChangeStatistic,
    PeerComparisonStatistic,
    StatisticView,
)


@dataclass(frozen=True)
class PublishedMeasurement:
    publication_id: UUID
    reviewer_id: UUID
    artifact_id: UUID
    rubric_id: UUID
    published_at: datetime
    opened_at: datetime
    submitted_at: datetime
    review_deadline: datetime | None
    attempt: int
    # criterion id -> title, maximum, human points, chosen AI points (if measured)
    decisions: dict[UUID, tuple[str, float, float, float | None]]


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def minutes_since(end: datetime, start: datetime) -> float | None:
    return (end - start).total_seconds() / 60 if end >= start else None


def peer_comparison(
    selected: list[PublishedMeasurement], population: list[PublishedMeasurement]
) -> PeerComparisonStatistic:
    # Keep only the latest published opinion by each reviewer for an exact input.
    latest: dict[tuple[UUID, UUID, UUID], PublishedMeasurement] = {}
    for record in sorted(population, key=lambda item: (item.published_at, item.publication_id)):
        latest[record.artifact_id, record.rubric_id, record.reviewer_id] = record
    groups: dict[tuple[UUID, UUID], list[PublishedMeasurement]] = defaultdict(list)
    for record in latest.values():
        groups[record.artifact_id, record.rubric_id].append(record)
    selected_ids = {record.publication_id for record in selected}
    differences: dict[UUID, list[float]] = defaultdict(list)
    titles: dict[UUID, str] = {}
    for record in latest.values():
        if record.publication_id not in selected_ids:
            continue
        for criterion_id, (title, maximum, points, _) in record.decisions.items():
            if maximum <= 0:
                continue
            for peer in groups[record.artifact_id, record.rubric_id]:
                if (
                    peer.artifact_id != record.artifact_id
                    or peer.rubric_id != record.rubric_id
                    or peer.reviewer_id == record.reviewer_id
                ):
                    continue
                peer_decision = peer.decisions.get(criterion_id)
                if peer_decision is None or peer_decision[1] != maximum:
                    continue
                differences[criterion_id].append((points - peer_decision[2]) / maximum * 100)
                titles[criterion_id] = title
    values = [abs(value) for criterion in differences.values() for value in criterion]
    return PeerComparisonStatistic(
        sample_count=len(values),
        divergence_percent=mean(values),
        stricter_criteria=[titles[key] for key, values in differences.items() if sum(values) < 0],
        softer_criteria=[titles[key] for key, values in differences.items() if sum(values) > 0],
        fully_agreed_criteria=sum(
            all(value == 0 for value in values) for values in differences.values()
        ),
        compared_criteria=len(differences),
    )


def summarize_statistics(
    records: list[PublishedMeasurement],
    reviewer_id: UUID | None,
    start: datetime,
    end: datetime,
) -> StatisticView:
    selected = [
        record for record in records if reviewer_id is None or record.reviewer_id == reviewer_id
    ]

    def durations(items: list[PublishedMeasurement], *, waiting: bool) -> list[float]:
        return [
            value
            for item in items
            if (
                value := minutes_since(
                    item.published_at, item.submitted_at if waiting else item.opened_at
                )
            )
            is not None
        ]

    def comparisons(items: list[PublishedMeasurement]) -> list[bool]:
        return [
            points != ai
            for item in items
            for _, _, points, ai in item.decisions.values()
            if ai is not None
        ]

    def acceptance(values: list[bool]) -> float | None:
        return 100 * (len(values) - sum(values)) / len(values) if values else None

    compared = comparisons(selected)
    elapsed, waiting = durations(selected, waiting=False), durations(selected, waiting=True)
    changes: dict[UUID, list[bool]] = defaultdict(list)
    titles: dict[UUID, str] = {}
    for item in selected:
        for identity, (title, _, points, ai) in item.decisions.items():
            if ai is not None:
                changes[identity].append(points != ai)
                titles[identity] = title
    peer = peer_comparison(selected, records)
    peer.course_divergence_percent = peer_comparison(records, records).divergence_percent
    measured_deadlines = [item for item in selected if item.review_deadline is not None]
    course_deadlines = [item for item in records if item.review_deadline is not None]
    reviewers = {item.reviewer_id for item in course_deadlines}
    course_overdue = sum(
        item.published_at > item.review_deadline
        for item in course_deadlines
        if item.review_deadline is not None
    )
    return StatisticView(
        publications=len(selected),
        repeated_publications=sum(item.attempt > 1 for item in selected),
        average_elapsed_minutes=mean(elapsed),
        elapsed_sample_count=len(elapsed),
        average_wait_minutes=mean(waiting),
        wait_sample_count=len(waiting),
        overdue_publications=sum(
            item.published_at > item.review_deadline
            for item in measured_deadlines
            if item.review_deadline is not None
        ),
        overdue_sample_count=len(measured_deadlines),
        ai_acceptance_percent=acceptance(compared),
        changed_decisions=sum(compared),
        compared_decisions=len(compared),
        course_average_elapsed_minutes=mean(durations(records, waiting=False)),
        course_average_wait_minutes=mean(durations(records, waiting=True)),
        course_ai_acceptance_percent=acceptance(comparisons(records)),
        course_average_overdue_publications=course_overdue / len(reviewers) if reviewers else None,
        criterion_changes=[
            CriterionChangeStatistic(
                criterion_id=identity,
                title=titles[identity],
                compared_works=len(values),
                changed_works=sum(values),
                change_percent=100 * sum(values) / len(values),
            )
            for identity, values in sorted(
                changes.items(), key=lambda item: (-sum(item[1]) / len(item[1]), str(item[0]))
            )
        ],
        peer_comparison=peer,
        from_date=start,
        until_date=end,
    )
