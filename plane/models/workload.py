"""Models for the workload feature: per-work-item time estimates, the
assignee x period workload matrix, and parent-rollup aggregation.

Mirrors the server-side semantics implemented in ``apps/api/plane/workload/``
of the plane fork (``aggregation.py`` / ``rollup.py`` / ``service.py``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WorkloadGranularity = Literal["day", "week", "month"]


class WorkloadTask(BaseModel):
    """One work item on an assignee's row.

    ``hours`` is THIS assignee's share of the estimate, not the whole thing:
    a work item may carry several assignees and its hours split evenly
    across them, so a shared 8h item reports 4.0 on each of two rows.
    ``total_hours`` keeps the undivided figure.

    ``unestimated`` is ``True`` when the work item has no estimate row, or
    one with ``hours <= 0``. Such an item carries ``hours=0.0`` and
    ``total_hours=0.0`` and contributes to NO capacity figure — every bucket
    and total on the row is identical to a response without it.

    **Do not infer ``unestimated`` from ``hours == 0``.** A stored zero-hour
    estimate is a real, reachable state (counted separately in
    :attr:`WorkloadMeta.zero_estimate_count`), so the arithmetic test
    misclassifies it. The flag is always sent; an estimated row carries
    ``False`` explicitly.

    ``state_color`` is the state's own colour and is a FREE-FORM CSS colour
    string, not a guaranteed hex — server-side it is an unvalidated
    ``CharField``, so ``""``, ``"#fa0"``, ``"rgb(...)"`` and named colours
    are all reachable. Do not parse it, and do not assume it is non-empty.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    project_id: str | None = None
    identifier: str | None = None
    name: str | None = None
    hours: float = 0.0
    total_hours: float = 0.0
    assignee_count: int = 1
    start_date: str | None = None
    target_date: str | None = None
    state_group: str | None = None
    state_name: str | None = None
    state_color: str | None = None
    unestimated: bool = False
    overdue: bool = False


class WorkloadRow(BaseModel):
    """One assignee's scheduled hours, bucketed by period.

    ``tasks`` is capped at 200 per assignee (``tasks_truncated`` reports the
    cap being hit). It is sorted with UNESTIMATED items first, and the cap is
    SHARED between the two kinds — so an assignee with a large unestimated
    backlog can have estimated rows truncated away, and ``tasks[0]`` is not
    the earliest-dated row.

    An empty row is not a gap in the data: every active, non-bot member of
    the in-scope projects gets a row whether or not they carry work, and the
    unused ``capacity_buckets`` is the point of it — that is how the response
    answers "who is free" as well as "who is overloaded".
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    assignee_id: str | None = None
    assignee_name: str | None = None
    buckets: dict[str, float] = Field(default_factory=dict)
    # Hours per calendar month ("2026-08"), independent of the requested
    # granularity — a week bucket is keyed by the date its week begins, so
    # summing week buckets for a month credits a straddling week entirely to
    # the month it started in.
    month_buckets: dict[str, float] = Field(default_factory=dict)
    total: float = 0.0
    capacity_buckets: dict[str, float] = Field(default_factory=dict)
    over: dict[str, bool] = Field(default_factory=dict)
    total_over: bool = False
    tasks: list["WorkloadTask"] = Field(default_factory=list)
    tasks_truncated: bool = False


class WorkloadUnscheduled(BaseModel):
    """Hours with no ``target_date`` for one assignee — excluded from any
    period bucket in the matrix."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    assignee_id: str | None = None
    hours: float = 0.0


class WorkloadMeta(BaseModel):
    """Diagnostic counters accompanying a workload matrix response."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # issues_counted and issues_unscheduled describe HOURS, so they count
    # estimated items only.
    issues_counted: int | None = None
    issues_unscheduled: int | None = None
    # Countable in-scope items with no usable estimate. A SUPERSET of
    # zero_estimate_count, which sees only stored rows with hours <= 0 and
    # not items carrying no estimate row at all.
    issues_unestimated: int | None = None
    dirty_date_count: int | None = None
    zero_estimate_count: int | None = None
    truncated: bool | None = None
    unscheduled_ratio: float | None = None


class WorkloadMatrixResponse(BaseModel):
    """Response for the workspace/project workload matrix endpoints.

    Counts LEAF work items only — a work item with one or more countable
    sub-items never contributes its own estimate to the matrix (its
    sub-items do, individually; the parent's aggregate is available via
    :meth:`~plane.api.workload.Workload.list_rollups`).

    There is NO default state filter: when ``state_group`` is omitted every
    group is returned, ``completed`` and ``cancelled`` included. An earlier
    version of this docstring claimed the two were excluded by default; the
    server has no such branch, and silently applying a filter the caller
    could neither see nor clear was the bug that removed it.

    ``rows`` counts PEOPLE, not work — every active, non-bot member of the
    in-scope projects gets a row whether or not they carry anything, so
    ``len(rows)`` is a headcount and answers nothing about whether this
    window holds work. To ask that::

        any(r.tasks or r.total for r in resp.rows)

    Both halves are needed: ``total`` alone misses a member whose only work
    is unscheduled or unestimated, and ``tasks`` alone misses hours whose
    rows were cut by the 200-per-assignee cap.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    granularity: WorkloadGranularity
    date_from: str
    date_to: str
    periods: list[str] = Field(default_factory=list)
    rows: list[WorkloadRow] = Field(default_factory=list)
    unscheduled: list[WorkloadUnscheduled] = Field(default_factory=list)
    meta: WorkloadMeta | None = None


class WorkloadRollup(BaseModel):
    """Aggregate rollup for a parent work item, computed over its full
    descendant tree (max depth 10, capped at 10,000 traversed rows).

    - ``hours``: sum of countable LEAF estimates (hours > 0) across the
      entire descendant tree.
    - ``done_hours``: subset of ``hours`` contributed by leaves whose state
      group is ``completed``.
    - ``percent``: ``round(done_hours / hours, 4)``; ``None`` when ``hours``
      is 0 (no countable estimates yet — avoids a division by zero).
    - ``due_date``: max ``target_date`` across ALL countable descendants
      (leaves and intermediate nodes), as an ISO date string, or ``None``.
    - ``leaf_count``: count of countable leaves with hours > 0.

    A descendant is "countable" when it is not soft-deleted, not archived,
    not a draft, and its state group is not ``cancelled``/``triage``.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    hours: float
    done_hours: float
    percent: float | None = None
    due_date: str | None = None
    leaf_count: int


class WorkloadEstimateDetail(BaseModel):
    """Response for the single work-item ``workload-estimate`` GET/PUT
    endpoints.

    ``hours`` is ``None`` when no estimate is stored, and is ALWAYS ``None``
    for a parent work item (``is_parent=True``) — a legacy stored value
    never leaks once a work item has countable sub-items; set estimates on
    the sub-items instead. ``is_parent`` / ``rollup`` are populated on GET
    only; the PUT response never carries them (a PUT can only succeed
    against a leaf work item — see
    :class:`~plane.errors.errors.WorkloadParentHasChildrenError`).
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = None
    work_item_id: str | None = Field(default=None, alias="issue")
    hours: float | None = None
    created_at: str | None = None
    updated_at: str | None = None
    is_parent: bool | None = None
    rollup: WorkloadRollup | None = None


class UpdateWorkloadEstimate(BaseModel):
    """Request body for ``PUT .../workload-estimate/``."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    hours: float = Field(..., ge=0, le=10000)


class WorkloadQueryParams(BaseModel):
    """Query parameters for the workspace/project workload matrix
    endpoints.

    ``granularity``, ``date_from``, and ``date_to`` are required. Date span
    is capped server-side depending on granularity (92 days for ``day``,
    366 for ``week``, 730 for ``month``).
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    granularity: WorkloadGranularity
    date_from: str
    date_to: str
    project_ids: list[str] | None = Field(
        default=None,
        description="Work item project UUIDs to narrow the matrix to. CSV-joined on the wire.",
    )
    assignee_ids: list[str] | None = Field(
        default=None,
        description="Assignee UUIDs to narrow the matrix to. CSV-joined on the wire.",
    )
    state_group: list[str] | None = Field(
        default=None,
        description=(
            "State groups to include (overrides the completed/cancelled "
            "default exclusion). CSV-joined on the wire."
        ),
    )


__all__ = [
    "WorkloadGranularity",
    "WorkloadRow",
    "WorkloadUnscheduled",
    "WorkloadMeta",
    "WorkloadMatrixResponse",
    "WorkloadRollup",
    "WorkloadEstimateDetail",
    "UpdateWorkloadEstimate",
    "WorkloadQueryParams",
]
