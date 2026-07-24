from collections.abc import Mapping
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import Configuration
from ..errors.errors import HttpError


class BaseResource:
    def __init__(self, config: Configuration, base_path: str, *, versioned: bool = True) -> None:
        self.config = config
        self.base_path = base_path.rstrip("/")
        # Almost every resource is mounted under the versioned /api/v1/ root
        # (``config.base_path``). A small number of fork add-on endpoints
        # (e.g. github_ext) mount directly under /api/ instead — pass
        # ``versioned=False`` to target ``config.root_path`` there.
        self._root_path = config.base_path if versioned else config.root_path
        self.session = requests.Session()

        if self.config.retry:
            retry = Retry(
                total=self.config.retry.total,
                backoff_factor=self.config.retry.backoff_factor,
                status_forcelist=self.config.retry.status_forcelist,
                allowed_methods=self.config.retry.allowed_methods,
                respect_retry_after_header=True,
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry)
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)

    # HTTP methods
    def _get(self, endpoint: str, params: Mapping[str, Any] | None = None) -> Any:
        url = self._build_url(endpoint)
        response = self.session.get(
            url, headers=self._headers(), params=params, timeout=self.config.timeout
        )
        return self._handle_response(response)

    def _post(self, endpoint: str, data: Mapping[str, Any] | list[Any] | None = None) -> Any:
        url = self._build_url(endpoint)
        response = self.session.post(
            url, headers=self._headers(), json=data, timeout=self.config.timeout
        )
        return self._handle_response(response)

    def _put(self, endpoint: str, data: Mapping[str, Any] | None = None) -> Any:
        url = self._build_url(endpoint)
        response = self.session.put(
            url, headers=self._headers(), json=data, timeout=self.config.timeout
        )
        return self._handle_response(response)

    def _patch(self, endpoint: str, data: Mapping[str, Any] | None = None) -> Any:
        url = self._build_url(endpoint)
        response = self.session.patch(
            url, headers=self._headers(), json=data, timeout=self.config.timeout
        )
        return self._handle_response(response)

    def _delete(self, endpoint: str, data: Mapping[str, Any] | None = None) -> None:
        url = self._build_url(endpoint)
        response = self.session.delete(
            url, headers=self._headers(), json=data, timeout=self.config.timeout
        )
        self._handle_response(response)

    # Helpers
    def _build_url(self, endpoint: str) -> str:
        endpoint = endpoint.strip("/")
        base = f"{self._root_path.rstrip('/')}{self.base_path}/"
        return f"{base}{endpoint}/" if endpoint else base

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["X-Api-Key"] = self.config.api_key
        if self.config.access_token:
            headers["Authorization"] = f"Bearer {self.config.access_token}"
        return headers

    def _handle_response(self, response: requests.Response) -> Any:
        if response.status_code == 204:
            return None
        if 200 <= response.status_code < 300:
            if not response.content:
                return None
            if "application/json" in response.headers.get("content-type", "").lower():
                return response.json()
            return response.text
        try:
            payload = response.json()
        except Exception:
            payload = response.text
        raise HttpError(
            f"HTTP {response.status_code}: {response.reason}",
            response.status_code,
            payload,
        )
