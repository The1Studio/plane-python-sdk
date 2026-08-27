"""API client for the github_ext status-automation config surface:
GitHub PR-lifecycle -> work-item state transition rules, configurable at
three scope tiers (instance -> workspace -> project, most specific wins).

Mounted at ``/api/github/...`` — NOT ``/api/v1/...`` like every other
resource in this SDK — by the plane fork's ``github_ext`` Django app (see
``apps/api/plane/github_ext/urls.py``, included from core ``urls.py`` as
``path("api/", include("plane.github_ext.urls"))``). This resource is
therefore constructed with ``versioned=False`` so it targets
:attr:`~plane.config.Configuration.root_path` instead of the versioned
``base_path`` every other resource uses.

.. note::
   As merged, the three config views (``GithubGlobalConfigView`` /
   ``GithubWorkspaceConfigView`` / ``GithubProjectConfigView``) extend the
   *app-internal* ``plane.app.views.base.BaseAPIView``, which authenticates
   via ``BaseSessionAuthentication`` (Django session cookie) — not the
   ``APIKeyAuthentication`` the rest of the public ``/api/v1/`` surface (and
   this SDK's ``api_key`` / ``access_token`` auth) uses. These bindings are
   implemented to the documented URL/method/payload contract, but a request
   authenticated with ``X-Api-Key`` or ``Authorization: Bearer`` will 401
   against a real deployment until the server-side views are updated to
   accept that auth. Flagged upstream; not something this SDK can work
   around.
"""

from __future__ import annotations

from typing import Any

from ..models.github import StateTransitionRules
from .base_resource import BaseResource


class GithubConfig(BaseResource):
    """Resource for the github_ext three-tier state-transition config.

    Every ``get_*_config`` method returns the **resolved** rules visible at
    that tier — built-in defaults merged with every applicable override at
    or below it (instance -> workspace -> project, most specific wins) —
    not merely what is stored at the requested tier. Every ``set_*_config``
    method upserts only that tier's own override row; it never affects the
    tiers above or below it.
    """

    def __init__(self, config: Any) -> None:
        super().__init__(config, "/github/", versioned=False)

    # ── Instance tier ────────────────────────────────────────────

    def get_instance_config(self) -> StateTransitionRules:
        """Read the instance-wide default rules.

        ``GET /api/github/config/`` — requires instance-admin auth. This is
        the top of the resolution chain, so the response is the stored
        ``scope="global"`` row merged over the server's built-in defaults.
        """
        response = self._get("config")
        return StateTransitionRules.model_validate(response["rules"])

    def set_instance_config(self, rules: StateTransitionRules) -> StateTransitionRules:
        """Upsert the instance-wide default rules.

        ``PUT /api/github/config/`` — requires instance-admin auth.

        Args:
            rules: Partial or full event -> state-name mapping.
        """
        response = self._put("config", {"rules": rules.to_rules()})
        return StateTransitionRules.model_validate(response["rules"])

    # ── Workspace tier ───────────────────────────────────────────

    def get_workspace_config(self, workspace_slug: str) -> StateTransitionRules:
        """Read the resolved (defaults -> instance -> workspace) rules
        visible to this workspace.

        ``GET /api/github/<slug>/config/`` — any workspace member.

        Args:
            workspace_slug: The workspace slug identifier.
        """
        response = self._get(f"{workspace_slug}/config")
        return StateTransitionRules.model_validate(response["rules"])

    def set_workspace_config(
        self, workspace_slug: str, rules: StateTransitionRules
    ) -> StateTransitionRules:
        """Upsert this workspace's override rules.

        ``PUT /api/github/<slug>/config/`` — requires workspace-admin.

        Validated for shape only server-side — a workspace override is
        project-agnostic, so state *names* are not checked against any
        project's ``State`` rows here (that happens at the project tier).

        Args:
            workspace_slug: The workspace slug identifier.
            rules: Partial or full event -> state-name mapping.
        """
        response = self._put(f"{workspace_slug}/config", {"rules": rules.to_rules()})
        return StateTransitionRules.model_validate(response["rules"])

    # ── Project tier ─────────────────────────────────────────────

    def get_project_config(self, workspace_slug: str, project_id: str) -> StateTransitionRules:
        """Read the fully-resolved effective rules for this project
        (defaults -> instance -> workspace -> project).

        ``GET /api/github/<slug>/projects/<project_id>/config/`` — any
        workspace member.

        Args:
            workspace_slug: The workspace slug identifier.
            project_id: UUID of the project.
        """
        response = self._get(f"{workspace_slug}/projects/{project_id}/config")
        return StateTransitionRules.model_validate(response["rules"])

    def set_project_config(
        self, workspace_slug: str, project_id: str, rules: StateTransitionRules
    ) -> StateTransitionRules:
        """Upsert this project's override rules.

        ``PUT /api/github/<slug>/projects/<project_id>/config/`` —
        requires workspace-admin.

        Unlike the workspace tier, each state name IS validated
        server-side against this project's ``State`` rows — an
        unrecognized state name is rejected with a 400.

        Args:
            workspace_slug: The workspace slug identifier.
            project_id: UUID of the project.
            rules: Partial or full event -> state-name mapping.
        """
        response = self._put(
            f"{workspace_slug}/projects/{project_id}/config",
            {"rules": rules.to_rules()},
        )
        return StateTransitionRules.model_validate(response["rules"])


__all__ = ["GithubConfig"]
