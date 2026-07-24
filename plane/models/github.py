"""Models for the github_ext status-automation config surface: which Plane
state a GitHub PR lifecycle event transitions a work item to, configurable
at three scope tiers (instance -> workspace -> project).

Mirrors the server-side contract implemented in
``apps/api/plane/github_ext/views/config.py`` /
``services/state_transition.py`` of the plane fork
(``The1Studio/plane@company-main``).
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

GithubEventKey = Literal["pr_opened", "pr_ready_for_review", "pr_merged"]
"""The closed set of GitHub PR lifecycle events the server accepts as
``rules`` keys. No other event key is valid at any scope tier."""

GITHUB_EVENT_KEYS: tuple[GithubEventKey, ...] = get_args(GithubEventKey)

DEFAULT_STATE_TRANSITION_RULES: dict[str, str] = {
    "pr_opened": "In Progress",
    "pr_ready_for_review": "In Review",
    "pr_merged": "Done",
}
"""The server's built-in defaults, applied when no tier overrides an event."""


class StateTransitionRules(BaseModel):
    """A (possibly partial) mapping of GitHub PR lifecycle events to Plane
    state *names*.

    Used both as the request body for every ``set_*_config`` call and as
    the response shape for every ``get_*_config`` call on
    :class:`~plane.api.github.GithubConfig` — the wire shape is identical
    both ways: ``{"rules": {...}}``, unwrapped to this model's fields.

    All three fields are optional because a *stored override* is partial by
    design — an instance/workspace/project override only needs to carry the
    events it wants to change; the server resolves the effective rules by
    merging built-in defaults -> instance -> workspace -> project
    (most-specific tier wins for any event key it sets). A ``get_*_config``
    response reflects that *resolved* merge, not merely what is stored at
    the requested tier — see the docstrings on
    :class:`~plane.api.github.GithubConfig`.

    The event-key set is closed (exactly ``pr_opened``,
    ``pr_ready_for_review``, ``pr_merged`` — see :data:`GithubEventKey`);
    unrecognized keys in a server response are dropped rather than raising,
    matching this SDK's ``extra="ignore"`` convention for request/response
    DTOs with a fixed, enumerable shape.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    pr_opened: str | None = None
    pr_ready_for_review: str | None = None
    pr_merged: str | None = None

    def to_rules(self) -> dict[str, str]:
        """Serialize to the wire ``rules`` dict, omitting unset events."""
        return self.model_dump(exclude_none=True)


__all__ = [
    "GithubEventKey",
    "GITHUB_EVENT_KEYS",
    "DEFAULT_STATE_TRANSITION_RULES",
    "StateTransitionRules",
]
