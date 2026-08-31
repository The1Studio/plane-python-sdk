"""Models for the module-cascade endpoints of the ``cascade_ext`` app: what
would cascade if a module went terminal, and the atomic apply that makes it
so.

Mirrors the server-side contract implemented in
``apps/api/plane/cascade_ext/service.py`` / ``views.py`` of the plane fork
branch ``feat/module-cascade-terminal-status`` (``The1Studio/plane``).

Two routes, both mounted at the UNVERSIONED ``/api/cascade-ext/`` root (NOT
``/api/v1/``):

- ``GET .../modules/<module_id>/cascade-preview/?status=<completed|cancelled>``
- ``POST .../modules/<module_id>/cascade-apply/``  ``{"status", "item_ids"}``

The preview query parameter is ``status`` (a MODULE status) — deliberately
NOT the per-issue cascade routes' ``group``.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

ModuleCascadeStatus = Literal["completed", "cancelled"]
"""The closed set of MODULE statuses the module-cascade endpoints accept.
Both the preview query parameter and the apply body name a module *status*,
not a state group — a completed module cascades ``completed`` onto its
items, a cancelled module cascades ``cancelled`` (the mirror of the
server's ``MODULE_STATUS_TO_STATE_GROUP``)."""

MODULE_CASCADE_STATUSES: tuple[ModuleCascadeStatus, ...] = get_args(ModuleCascadeStatus)

ModuleCascadeItemReason = Literal["no_matching_state", "no_permission"]
"""``reason`` on a PREVIEW item: ``None`` for an eligible item, or one of
these two values for an ineligible one."""

ModuleCascadeRejectReason = Literal[
    "no_matching_state",
    "no_permission",
    "already_terminal",
    "under_terminal_ancestor",
    "not_in_module_tree",
    "not_eligible",
]
"""``reason`` on an APPLY response ``rejected[]`` entry — the server's closed
set. Notably includes ``under_terminal_ancestor`` (the Phase-0 behavior
change: a live item beneath an already-terminal ancestor is left live where
it used to be swept, and posting its id is refused with this reason) and
``already_terminal``."""

MODULE_CASCADE_REJECT_REASONS: tuple[ModuleCascadeRejectReason, ...] = get_args(
    ModuleCascadeRejectReason
)


class ModuleCascadeItem(BaseModel):
    """One candidate work item in a cascade preview.

    ``is_module_member`` is emitted explicitly by the server (a descendant
    that is ALSO a module member gets the flag rather than it being inferred
    from ``depth == 0``). An item with ``eligible=False`` carries a
    ``reason`` of ``"no_matching_state"`` (its project has no state in the
    target group) or ``"no_permission"`` (the actor is not an active member
    of its project); an eligible item carries ``reason=None``.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    identifier: str
    name: str
    depth: int
    is_module_member: bool
    project_id: str
    project_name: str
    state_id: str | None = None
    state_name: str | None = None
    state_group: str | None = None
    target_state_id: str | None = None
    eligible: bool
    reason: ModuleCascadeItemReason | None = None


class ModuleCascadeSummary(BaseModel):
    """Live / eligible / ineligible / already-terminal counts for a preview."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    total_live: int
    eligible: int
    ineligible: int
    already_terminal: int


class ModuleCascadePreview(BaseModel):
    """Response for ``GET .../cascade-preview/``.

    ``items`` is EMPTY when ``over_cap`` is true — the server refuses to
    truncate (a truncated list would silently under-report what a confirm
    would write); the real count stays in ``summary.total_live``. Callers
    must branch on ``over_cap`` / the summary, never on ``len(items)``.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    target_group: str
    depth_capped: bool
    over_cap: bool
    cap: int
    summary: ModuleCascadeSummary
    items: list[ModuleCascadeItem] = Field(default_factory=list)


class ModuleCascadeApplyData(BaseModel):
    """Request body for ``POST .../cascade-apply/``.

    ``item_ids`` omitted or ``None`` cascades every currently-eligible item
    (headless/MCP callers); an explicit ``[]`` cascades none — only the
    module's status moves.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    status: ModuleCascadeStatus
    item_ids: list[str] | None = None


class ModuleCascadeApplyRejected(BaseModel):
    """One requested-but-refused id on an apply response, with its reason."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    reason: ModuleCascadeRejectReason


class ModuleCascadeApplyResponse(BaseModel):
    """Response for ``POST .../cascade-apply/``.

    ``module`` is the module's id; ``updated`` lists the ids that were
    actually cascaded; ``rejected`` carries every requested-but-refused id
    with its reason.

    When the live count exceeds the server hard cap, the server returns 400
    with NOTHING written (module status included); that surfaces as
    :class:`~plane.errors.errors.ModuleCascadeCapExceeded` rather than this
    response shape.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    module: str
    status: ModuleCascadeStatus
    updated: list[str] = Field(default_factory=list)
    rejected: list[ModuleCascadeApplyRejected] = Field(default_factory=list)


__all__ = [
    "ModuleCascadeStatus",
    "MODULE_CASCADE_STATUSES",
    "ModuleCascadeItemReason",
    "ModuleCascadeRejectReason",
    "MODULE_CASCADE_REJECT_REASONS",
    "ModuleCascadeItem",
    "ModuleCascadeSummary",
    "ModuleCascadePreview",
    "ModuleCascadeApplyData",
    "ModuleCascadeApplyRejected",
    "ModuleCascadeApplyResponse",
]
