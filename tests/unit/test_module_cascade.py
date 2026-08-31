"""Unit tests for the ModuleCascade API resource (cascade_ext module
cascade-preview/cascade-apply): offline model-validation, URL-construction,
and request-routing tests, plus live smoke tests that skip without
configured credentials.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from plane.api.module_cascade import ModuleCascade
from plane.client import PlaneClient
from plane.config import Configuration
from plane.errors.errors import HttpError, ModuleCascadeCapExceeded
from plane.models.module_cascade import (
    MODULE_CASCADE_REJECT_REASONS,
    MODULE_CASCADE_STATUSES,
    ModuleCascadeApplyData,
    ModuleCascadeApplyResponse,
    ModuleCascadeItem,
    ModuleCascadePreview,
)


class TestModuleCascadeModelValidation:
    """Offline Pydantic model-validation tests (no network)."""

    def test_preview_fully_populated(self) -> None:
        preview = ModuleCascadePreview.model_validate(
            {
                "target_group": "completed",
                "depth_capped": False,
                "over_cap": False,
                "cap": 100,
                "summary": {
                    "total_live": 47,
                    "eligible": 44,
                    "ineligible": 3,
                    "already_terminal": 12,
                },
                "items": [
                    {
                        "id": "wi-1",
                        "identifier": "AUT-3",
                        "name": "Child task",
                        "depth": 0,
                        "is_module_member": True,
                        "project_id": "proj-1",
                        "project_name": "Auth",
                        "state_id": "st-1",
                        "state_name": "Todo",
                        "state_group": "unstarted",
                        "target_state_id": "st-2",
                        "eligible": True,
                        "reason": None,
                    },
                    {
                        "id": "wi-2",
                        "identifier": "AUT-7",
                        "name": "Foreign issue",
                        "depth": 1,
                        "is_module_member": False,
                        "project_id": "proj-2",
                        "project_name": "Billing",
                        "state_id": "st-3",
                        "state_name": "Todo",
                        "state_group": "unstarted",
                        "target_state_id": None,
                        "eligible": False,
                        "reason": "no_matching_state",
                    },
                ],
            }
        )
        assert preview.target_group == "completed"
        assert preview.over_cap is False
        assert preview.cap == 100
        assert preview.summary.total_live == 47
        assert preview.summary.eligible == 44
        assert preview.summary.ineligible == 3
        assert preview.summary.already_terminal == 12
        assert len(preview.items) == 2
        assert preview.items[0].is_module_member is True
        assert preview.items[0].eligible is True
        assert preview.items[0].reason is None
        assert preview.items[1].reason == "no_matching_state"
        assert isinstance(preview.items[0], ModuleCascadeItem)

    def test_preview_over_cap_has_empty_items(self) -> None:
        """Over the cap the server returns an EMPTY items list (refusal, not
        truncation) — the real count stays in summary.total_live."""
        preview = ModuleCascadePreview.model_validate(
            {
                "target_group": "completed",
                "depth_capped": False,
                "over_cap": True,
                "cap": 100,
                "summary": {
                    "total_live": 147,
                    "eligible": 140,
                    "ineligible": 7,
                    "already_terminal": 3,
                },
                "items": [],
            }
        )
        assert preview.over_cap is True
        assert preview.items == []
        assert preview.summary.total_live == 147

    def test_preview_reason_no_permission(self) -> None:
        preview = ModuleCascadePreview.model_validate(
            {
                "target_group": "cancelled",
                "depth_capped": False,
                "over_cap": False,
                "cap": 100,
                "summary": {
                    "total_live": 1,
                    "eligible": 0,
                    "ineligible": 1,
                    "already_terminal": 0,
                },
                "items": [
                    {
                        "id": "wi-9",
                        "identifier": "AUT-9",
                        "name": "Restricted",
                        "depth": 1,
                        "is_module_member": False,
                        "project_id": "proj-2",
                        "project_name": "Billing",
                        "state_id": "st-3",
                        "state_name": "Todo",
                        "state_group": "unstarted",
                        "target_state_id": "st-4",
                        "eligible": False,
                        "reason": "no_permission",
                    }
                ],
            }
        )
        assert preview.items[0].reason == "no_permission"

    def test_apply_response_round_trip(self) -> None:
        response = ModuleCascadeApplyResponse.model_validate(
            {
                "module": "mod-1",
                "status": "completed",
                "updated": ["wi-1"],
                "rejected": [
                    {"id": "wi-2", "reason": "no_matching_state"},
                    {"id": "wi-3", "reason": "under_terminal_ancestor"},
                    {"id": "wi-4", "reason": "already_terminal"},
                    {"id": "wi-5", "reason": "not_in_module_tree"},
                    {"id": "wi-6", "reason": "not_eligible"},
                    {"id": "wi-7", "reason": "no_permission"},
                ],
            }
        )
        assert response.module == "mod-1"
        assert response.status == "completed"
        assert response.updated == ["wi-1"]
        assert [r.reason for r in response.rejected] == [
            "no_matching_state",
            "under_terminal_ancestor",
            "already_terminal",
            "not_in_module_tree",
            "not_eligible",
            "no_permission",
        ]

    def test_apply_defaults_empty_updated_and_rejected(self) -> None:
        response = ModuleCascadeApplyResponse.model_validate(
            {"module": "mod-1", "status": "cancelled", "updated": [], "rejected": []}
        )
        assert response.updated == []
        assert response.rejected == []

    def test_apply_data_item_ids_optional(self) -> None:
        data = ModuleCascadeApplyData(status="completed")
        assert data.item_ids is None

    def test_apply_data_item_ids_empty_list(self) -> None:
        data = ModuleCascadeApplyData(status="cancelled", item_ids=[])
        assert data.item_ids == []

    def test_apply_data_rejects_bad_status(self) -> None:
        with pytest.raises(ValidationError):
            ModuleCascadeApplyData(status="started")  # type: ignore[arg-type]

    def test_module_cascade_statuses_closed_set(self) -> None:
        assert set(MODULE_CASCADE_STATUSES) == {"completed", "cancelled"}

    def test_reject_reasons_include_under_terminal_ancestor(self) -> None:
        assert "under_terminal_ancestor" in MODULE_CASCADE_REJECT_REASONS
        assert "already_terminal" in MODULE_CASCADE_REJECT_REASONS


class TestModuleCascadeURLConstruction:
    """Offline: verify the resource is mounted at /api/cascade-ext/... (the
    unversioned root), NOT /api/v1/cascade-ext/... like the versioned
    resources — composing against the versioned root would 404."""

    @pytest.fixture
    def resource(self) -> ModuleCascade:
        config = Configuration(base_path="https://api.plane.so", api_key="test-key")
        return ModuleCascade(config)

    def test_base_path_is_unversioned_cascade_ext(self) -> None:
        # BaseResource.__init__ stores base_path with the trailing slash
        # stripped (see base_resource.py) — the slash is re-added by
        # _build_url, not carried on the attribute itself.
        assert resource_base_path() == "/cascade-ext"

    def test_preview_url(self, resource: ModuleCascade) -> None:
        assert (
            resource._build_url("workspaces/acme/projects/proj-1/modules/mod-1/cascade-preview")
            == "https://api.plane.so/api/cascade-ext/workspaces/acme/projects/proj-1/modules/mod-1/cascade-preview/"
        )

    def test_apply_url(self, resource: ModuleCascade) -> None:
        assert (
            resource._build_url("workspaces/acme/projects/proj-1/modules/mod-1/cascade-apply")
            == "https://api.plane.so/api/cascade-ext/workspaces/acme/projects/proj-1/modules/mod-1/cascade-apply/"
        )


def resource_base_path() -> str:
    config = Configuration(base_path="https://api.plane.so", api_key="test-key")
    return ModuleCascade(config).base_path


class TestModuleCascadeRequestRouting:
    """Offline: verify preview/apply hit the right URL/method, send the
    right query/payload, and unwrap the response — via a mocked session, no
    network."""

    @pytest.fixture
    def resource(self) -> ModuleCascade:
        config = Configuration(base_path="https://api.plane.so", api_key="test-key")
        return ModuleCascade(config)

    @staticmethod
    def _mock_response(body: dict, status: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status
        resp.content = json.dumps(body).encode()
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = body
        return resp

    PREVIEW_BODY = {
        "target_group": "completed",
        "depth_capped": False,
        "over_cap": False,
        "cap": 100,
        "summary": {
            "total_live": 1,
            "eligible": 1,
            "ineligible": 0,
            "already_terminal": 0,
        },
        "items": [
            {
                "id": "wi-1",
                "identifier": "AUT-3",
                "name": "Child task",
                "depth": 0,
                "is_module_member": True,
                "project_id": "proj-1",
                "project_name": "Auth",
                "state_id": "st-1",
                "state_name": "Todo",
                "state_group": "unstarted",
                "target_state_id": "st-2",
                "eligible": True,
                "reason": None,
            }
        ],
    }

    APPLY_BODY = {
        "module": "mod-1",
        "status": "completed",
        "updated": ["wi-1"],
        "rejected": [],
    }

    def test_preview_hits_url_with_status_param(self, resource: ModuleCascade) -> None:
        resource.session.get = MagicMock(return_value=self._mock_response(self.PREVIEW_BODY))
        result = resource.preview("acme", "proj-1", "mod-1", "completed")
        assert isinstance(result, ModuleCascadePreview)
        assert result.target_group == "completed"
        assert result.summary.eligible == 1
        called_url = resource.session.get.call_args.args[0]
        called_params = resource.session.get.call_args.kwargs["params"]
        assert (
            called_url
            == "https://api.plane.so/api/cascade-ext/workspaces/acme/projects/proj-1/modules/mod-1/cascade-preview/"
        )
        # The preview query parameter is `status`, NOT `group`.
        assert called_params == {"status": "completed"}

    def test_preview_cancelled_status(self, resource: ModuleCascade) -> None:
        body = dict(self.PREVIEW_BODY)
        body["target_group"] = "cancelled"
        resource.session.get = MagicMock(return_value=self._mock_response(body))
        result = resource.preview("acme", "proj-1", "mod-1", "cancelled")
        assert result.target_group == "cancelled"
        assert resource.session.get.call_args.kwargs["params"] == {"status": "cancelled"}

    def test_preview_rejects_invalid_status_value(self, resource: ModuleCascade) -> None:
        with pytest.raises(ValueError):
            resource.preview("acme", "proj-1", "mod-1", "backlog")

    def test_apply_all_eligible_when_item_ids_omitted(self, resource: ModuleCascade) -> None:
        """item_ids omitted/null = every eligible item: the payload body must
        NOT contain an `item_ids` key at all."""
        resource.session.post = MagicMock(return_value=self._mock_response(self.APPLY_BODY))
        result = resource.apply(
            "acme", "proj-1", "mod-1", ModuleCascadeApplyData(status="completed")
        )
        assert isinstance(result, ModuleCascadeApplyResponse)
        assert result.updated == ["wi-1"]
        called_url = resource.session.post.call_args.args[0]
        called_json = resource.session.post.call_args.kwargs["json"]
        assert (
            called_url
            == "https://api.plane.so/api/cascade-ext/workspaces/acme/projects/proj-1/modules/mod-1/cascade-apply/"
        )
        assert called_json == {"status": "completed"}

    def test_apply_empty_item_ids_sends_empty_list(self, resource: ModuleCascade) -> None:
        """item_ids=[] = none cascade; the JSON payload must carry the empty
        list explicitly."""
        resource.session.post = MagicMock(return_value=self._mock_response(self.APPLY_BODY))
        result = resource.apply(
            "acme",
            "proj-1",
            "mod-1",
            ModuleCascadeApplyData(status="completed", item_ids=[]),
        )
        assert isinstance(result, ModuleCascadeApplyResponse)
        called_json = resource.session.post.call_args.kwargs["json"]
        assert called_json == {"status": "completed", "item_ids": []}

    def test_apply_with_selected_item_ids(self, resource: ModuleCascade) -> None:
        resource.session.post = MagicMock(return_value=self._mock_response(self.APPLY_BODY))
        result = resource.apply(
            "acme",
            "proj-1",
            "mod-1",
            ModuleCascadeApplyData(status="completed", item_ids=["wi-1"]),
        )
        assert isinstance(result, ModuleCascadeApplyResponse)
        called_json = resource.session.post.call_args.kwargs["json"]
        assert called_json == {"status": "completed", "item_ids": ["wi-1"]}

    def test_apply_rejects_invalid_status_value(self, resource: ModuleCascade) -> None:
        with pytest.raises(ValueError):
            resource.apply(
                "acme",
                "proj-1",
                "mod-1",
                ModuleCascadeApplyData(status="paused"),  # type: ignore[arg-type]
            )

    def test_apply_over_cap_raises_cap_exceeded_refusal(self, resource: ModuleCascade) -> None:
        """Over the server's hard cap the apply returns 400 with
        {"error": "cascade exceeds MAX_MODULE_CASCADE_ITEMS", "total_live",
        "cap"} and writes NOTHING — the client surfaces it as a readable
        refusal naming the cap, not a generic HTTP error."""
        body = {"error": "cascade exceeds MAX_MODULE_CASCADE_ITEMS", "total_live": 147, "cap": 100}
        resource.session.post = MagicMock(return_value=self._mock_response(body, status=400))
        with pytest.raises(ModuleCascadeCapExceeded) as exc_info:
            resource.apply(
                "acme",
                "proj-1",
                "mod-1",
                ModuleCascadeApplyData(status="completed"),
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.total_live == 147
        assert exc_info.value.cap == 100
        assert "MAX_MODULE_CASCADE_ITEMS" in str(exc_info.value)

    def test_apply_archived_module_raises_plain_http_error(self, resource: ModuleCascade) -> None:
        """An archived module is refused with a 400 that is NOT the cap
        refusal — it must surface as the generic transport error so callers
        can branch differently."""
        body = {"error": "module is archived"}
        resource.session.post = MagicMock(return_value=self._mock_response(body, status=400))
        with pytest.raises(HttpError) as exc_info:
            resource.apply(
                "acme",
                "proj-1",
                "mod-1",
                ModuleCascadeApplyData(status="completed"),
            )
        assert exc_info.value.status_code == 400
        assert not isinstance(exc_info.value, ModuleCascadeCapExceeded)

    def test_apply_unwraps_rejected_reasons(self, resource: ModuleCascade) -> None:
        body = {
            "module": "mod-1",
            "status": "completed",
            "updated": ["wi-1"],
            "rejected": [
                {"id": "wi-9", "reason": "under_terminal_ancestor"},
                {"id": "wi-8", "reason": "not_in_module_tree"},
            ],
        }
        resource.session.post = MagicMock(return_value=self._mock_response(body))
        result = resource.apply(
            "acme",
            "proj-1",
            "mod-1",
            ModuleCascadeApplyData(status="completed", item_ids=["wi-1", "wi-9", "wi-8"]),
        )
        assert [r.id for r in result.rejected] == ["wi-9", "wi-8"]
        assert result.rejected[0].reason == "under_terminal_ancestor"
        # ModuleCascadeRejectReason is a typing.Literal alias, not a real
        # class — isinstance() against it raises TypeError. Assert closed-set
        # membership instead.
        assert result.rejected[0].reason in MODULE_CASCADE_REJECT_REASONS


class TestModuleCascadeLiveAPI:
    """Live smoke tests (real HTTP requests) — skip without
    PLANE_BASE_URL/credentials configured (see conftest.py)."""

    def test_preview_live(self, client: PlaneClient, workspace_slug: str, project) -> None:
        # Requires the fork server-side module routes; skipped without creds.
        result = client.module_cascade.preview(
            workspace_slug, project.id, "00000000-0000-0000-0000-000000000000", "completed"
        )
        assert isinstance(result, ModuleCascadePreview)

    def test_apply_live(self, client: PlaneClient, workspace_slug: str, project) -> None:
        result = client.module_cascade.apply(
            workspace_slug,
            project.id,
            "00000000-0000-0000-0000-000000000000",
            ModuleCascadeApplyData(status="completed", item_ids=[]),
        )
        assert isinstance(result, ModuleCascadeApplyResponse)
