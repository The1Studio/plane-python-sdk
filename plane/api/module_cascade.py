"""API client for the module-cascade endpoints of the ``cascade_ext`` app:
preview what a module going terminal would cascade, and atomically apply it.

Mounted at ``/api/cascade-ext/...`` — NOT ``/api/v1/...`` like every other
resource in this SDK — by the plane fork's ``cascade_ext`` Django app (see
``apps/api/plane/cascade_ext/urls.py``, included from core ``urls.py`` as
``path("api/", include("plane.cascade_ext.urls"))``). This resource is
therefore constructed with ``versioned=False`` so it targets
:attr:`~plane.config.Configuration.root_path` instead of the versioned
``base_path`` every other resource uses. Composing against the versioned
root would yield ``/api/v1/cascade-ext/...`` and 404.

.. note::
   These bindings are implemented to the documented URL/method/payload
   contract of the fork branch ``feat/module-cascade-terminal-status``
   (``The1Studio/plane``) — the two module routes added alongside the
   existing per-issue cascade routes.
"""

from __future__ import annotations

from typing import Any

from ..errors.errors import HttpError, ModuleCascadeCapExceeded
from ..models.module_cascade import (
    MODULE_CASCADE_STATUSES,
    ModuleCascadeApplyData,
    ModuleCascadeApplyResponse,
    ModuleCascadePreview,
)
from .base_resource import BaseResource

_CAP_ERROR = "cascade exceeds MAX_MODULE_CASCADE_ITEMS"


class ModuleCascade(BaseResource):
    """Resource for the module cascade endpoints (cascade_ext app).

    A module "going terminal" (to ``completed`` or ``cancelled``) can cascade
    that state onto its member work items and, recursively, their live
    descendants. These bindings expose the two-phase flow:

    1. :meth:`preview` — read-only, answers "what would cascade if this
       module went terminal right now", before the module's status changes.
    2. :meth:`apply` — atomically moves the module's status plus a
       caller-selected subset of the currently-eligible items.

    ``apply`` is capped server-side (``MAX_MODULE_CASCADE_ITEMS``): over the
    cap the server returns 400 having written NOTHING (module status
    included), which this client surfaces as
    :class:`~plane.errors.errors.ModuleCascadeCapExceeded` — a readable
    refusal naming the cap, not a generic HTTP error.
    """

    def __init__(self, config: Any) -> None:
        super().__init__(config, "/cascade-ext/", versioned=False)

    # ── Preview ───────────────────────────────────────────────────

    def preview(
        self, workspace_slug: str, project_id: str, module_id: str, status: str
    ) -> ModuleCascadePreview:
        """Preview what cascading a module's status to terminal would do.

        Read-only. ``GET /api/cascade-ext/workspaces/<slug>/projects/
        <project_id>/modules/<module_id>/cascade-preview/?status=<...>``.

        Args:
            workspace_slug: The workspace slug identifier.
            project_id: UUID of the project.
            module_id: UUID of the module.
            status: The module status to preview cascading onto its items —
                exactly one of ``"completed"`` or ``"cancelled"``.

        Raises:
            ValueError: ``status`` is not one of ``"completed"`` /
                ``"cancelled"``.
        """
        if status not in MODULE_CASCADE_STATUSES:
            raise ValueError(
                "status must be one of the module cascade statuses "
                f"{MODULE_CASCADE_STATUSES!r}, got {status!r}"
            )
        response = self._get(
            f"workspaces/{workspace_slug}/projects/{project_id}/modules/{module_id}/cascade-preview",
            params={"status": status},
        )
        return ModuleCascadePreview.model_validate(response)

    # ── Apply ─────────────────────────────────────────────────────

    def apply(
        self,
        workspace_slug: str,
        project_id: str,
        module_id: str,
        data: ModuleCascadeApplyData,
    ) -> ModuleCascadeApplyResponse:
        """Atomically move a module's status and cascade onto selected items.

        ``POST /api/cascade-ext/workspaces/<slug>/projects/<project_id>/
        modules/<module_id>/cascade-apply/``.

        ``item_ids`` is a REQUEST, never an authorization — eligibility is
        re-derived server-side, and any requested id that is not currently
        eligible lands in ``rejected`` with its reason rather than being
        silently applied or silently dropped:

        - ``item_ids=None`` (omitted) cascades every currently-eligible item.
        - ``item_ids=[]`` cascades none — only the module's status moves.

        Args:
            workspace_slug: The workspace slug identifier.
            project_id: UUID of the project.
            module_id: UUID of the module.
            data: The new module status and the optional item-id subset.

        Raises:
            ValueError: ``data.status`` is not one of ``"completed"`` /
                ``"cancelled"``.
            ModuleCascadeCapExceeded: the server's hard cap
                (``MAX_MODULE_CASCADE_ITEMS``) would be exceeded — the 400
                refuses the whole cascade and NOTHING is written, the
                module's own status included. The raised exception carries
                the live count (:attr:`total_live`) and the cap
                (:attr:`~ModuleCascadeCapExceeded.cap`).
            HttpError: the module is archived (400), not found (404), or any
                other non-2xx response.
        """
        if data.status not in MODULE_CASCADE_STATUSES:
            raise ValueError(
                "status must be one of the module cascade statuses "
                f"{MODULE_CASCADE_STATUSES!r}, got {data.status!r}"
            )
        payload = data.model_dump(exclude_none=True)
        try:
            response = self._post(
                f"workspaces/{workspace_slug}/projects/{project_id}/modules/{module_id}/cascade-apply",
                payload,
            )
        except HttpError as exc:
            if exc.status_code == 400 and isinstance(exc.response, dict):
                if exc.response.get("error") == _CAP_ERROR:
                    assert exc.status_code is not None
                    raise ModuleCascadeCapExceeded(str(exc), exc.status_code, exc.response) from exc
            raise
        return ModuleCascadeApplyResponse.model_validate(response)


__all__ = ["ModuleCascade"]
