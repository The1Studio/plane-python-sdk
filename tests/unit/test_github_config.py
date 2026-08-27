"""Unit tests for the GithubConfig API resource (github_ext status-automation
config): offline model/URL-construction/request-routing tests, plus live
smoke tests that skip without configured credentials.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from plane.api.github import GithubConfig
from plane.client import PlaneClient
from plane.config import Configuration
from plane.models.github import (
    DEFAULT_STATE_TRANSITION_RULES,
    GITHUB_EVENT_KEYS,
    StateTransitionRules,
)


class TestStateTransitionRulesModel:
    """Offline Pydantic model-validation tests (no network)."""

    def test_all_fields_optional(self) -> None:
        rules = StateTransitionRules()
        assert rules.pr_opened is None
        assert rules.pr_ready_for_review is None
        assert rules.pr_merged is None

    def test_to_rules_omits_unset_events(self) -> None:
        rules = StateTransitionRules(pr_opened="In Progress")
        assert rules.to_rules() == {"pr_opened": "In Progress"}

    def test_to_rules_full(self) -> None:
        rules = StateTransitionRules(
            pr_opened="In Progress",
            pr_ready_for_review="In Review",
            pr_merged="Done",
        )
        assert rules.to_rules() == {
            "pr_opened": "In Progress",
            "pr_ready_for_review": "In Review",
            "pr_merged": "Done",
        }

    def test_ignores_unrecognized_event_keys(self) -> None:
        """The event-key set is closed server-side; unrecognized keys in a
        response are dropped rather than raising."""
        rules = StateTransitionRules.model_validate(
            {"pr_opened": "In Progress", "pr_closed": "Cancelled"}
        )
        assert rules.pr_opened == "In Progress"
        assert not hasattr(rules, "pr_closed")

    def test_github_event_keys_matches_default_rules(self) -> None:
        assert set(GITHUB_EVENT_KEYS) == set(DEFAULT_STATE_TRANSITION_RULES)

    def test_default_rules_round_trip_through_model(self) -> None:
        rules = StateTransitionRules.model_validate(DEFAULT_STATE_TRANSITION_RULES)
        assert rules.to_rules() == DEFAULT_STATE_TRANSITION_RULES


class TestGithubConfigURLConstruction:
    """Offline: verify the resource is mounted at /api/github/... (the
    unversioned root), not /api/v1/github/... like every other resource in
    this SDK."""

    @pytest.fixture
    def resource(self) -> GithubConfig:
        config = Configuration(base_path="https://api.plane.so", api_key="test-key")
        return GithubConfig(config)

    def test_instance_config_url(self, resource: GithubConfig) -> None:
        assert resource._build_url("config") == "https://api.plane.so/api/github/config/"

    def test_workspace_config_url(self, resource: GithubConfig) -> None:
        assert resource._build_url("acme/config") == "https://api.plane.so/api/github/acme/config/"

    def test_project_config_url(self, resource: GithubConfig) -> None:
        assert (
            resource._build_url("acme/projects/proj-1/config")
            == "https://api.plane.so/api/github/acme/projects/proj-1/config/"
        )


class TestGithubConfigRequestRouting:
    """Offline: verify get_*/set_* methods hit the right URL/method, send
    the right payload, and unwrap the `rules` envelope — via a mocked
    session, no network."""

    @pytest.fixture
    def resource(self) -> GithubConfig:
        config = Configuration(base_path="https://api.plane.so", api_key="test-key")
        return GithubConfig(config)

    @staticmethod
    def _mock_response(body: dict) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.content = json.dumps(body).encode()
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = body
        return resp

    def test_get_instance_config(self, resource: GithubConfig) -> None:
        resource.session.get = MagicMock(
            return_value=self._mock_response({"rules": DEFAULT_STATE_TRANSITION_RULES})
        )
        result = resource.get_instance_config()
        assert isinstance(result, StateTransitionRules)
        assert result.pr_opened == "In Progress"
        called_url = resource.session.get.call_args.args[0]
        assert called_url == "https://api.plane.so/api/github/config/"

    def test_set_instance_config(self, resource: GithubConfig) -> None:
        resource.session.put = MagicMock(
            return_value=self._mock_response({"rules": {"pr_opened": "Doing"}})
        )
        result = resource.set_instance_config(StateTransitionRules(pr_opened="Doing"))
        assert result.pr_opened == "Doing"
        called_url = resource.session.put.call_args.args[0]
        called_json = resource.session.put.call_args.kwargs["json"]
        assert called_url == "https://api.plane.so/api/github/config/"
        assert called_json == {"rules": {"pr_opened": "Doing"}}

    def test_get_workspace_config(self, resource: GithubConfig) -> None:
        resource.session.get = MagicMock(
            return_value=self._mock_response({"rules": {"pr_merged": "Done"}})
        )
        result = resource.get_workspace_config("acme")
        assert result.pr_merged == "Done"
        called_url = resource.session.get.call_args.args[0]
        assert called_url == "https://api.plane.so/api/github/acme/config/"

    def test_set_workspace_config(self, resource: GithubConfig) -> None:
        resource.session.put = MagicMock(
            return_value=self._mock_response({"rules": {"pr_merged": "Shipped"}})
        )
        rules = StateTransitionRules(pr_merged="Shipped")
        result = resource.set_workspace_config("acme", rules)
        assert result.pr_merged == "Shipped"
        called_url = resource.session.put.call_args.args[0]
        called_json = resource.session.put.call_args.kwargs["json"]
        assert called_url == "https://api.plane.so/api/github/acme/config/"
        assert called_json == {"rules": {"pr_merged": "Shipped"}}

    def test_get_project_config(self, resource: GithubConfig) -> None:
        resource.session.get = MagicMock(
            return_value=self._mock_response({"rules": {"pr_opened": "In Progress"}})
        )
        result = resource.get_project_config("acme", "proj-1")
        assert result.pr_opened == "In Progress"
        called_url = resource.session.get.call_args.args[0]
        assert called_url == "https://api.plane.so/api/github/acme/projects/proj-1/config/"

    def test_set_project_config(self, resource: GithubConfig) -> None:
        resource.session.put = MagicMock(
            return_value=self._mock_response({"rules": {"pr_opened": "Todo"}})
        )
        rules = StateTransitionRules(pr_opened="Todo")
        result = resource.set_project_config("acme", "proj-1", rules)
        assert result.pr_opened == "Todo"
        called_url = resource.session.put.call_args.args[0]
        called_json = resource.session.put.call_args.kwargs["json"]
        assert called_url == "https://api.plane.so/api/github/acme/projects/proj-1/config/"
        assert called_json == {"rules": {"pr_opened": "Todo"}}


class TestGithubConfigLiveAPI:
    """Live smoke tests (real HTTP requests) — skip without
    PLANE_BASE_URL/credentials configured (see conftest.py).

    NOTE: as merged, the github_ext config views authenticate via Django
    session cookie (BaseSessionAuthentication), not the API-key/Bearer auth
    this SDK's `client` fixture uses — these will 401 against a real
    deployment until that's addressed server-side. Kept for structural
    parity with every other resource's test file and to exercise once the
    server-side auth gap is closed.
    """

    def test_get_workspace_config(self, client: PlaneClient, workspace_slug: str) -> None:
        result = client.github_config.get_workspace_config(workspace_slug)
        assert isinstance(result, StateTransitionRules)

    def test_get_project_config(self, client: PlaneClient, workspace_slug: str, project) -> None:
        result = client.github_config.get_project_config(workspace_slug, project.id)
        assert isinstance(result, StateTransitionRules)
