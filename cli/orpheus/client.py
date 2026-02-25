"""HTTP client for Orpheus server API."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import HttpUrl

from orpheus.config import load_config


if TYPE_CHECKING:
    from orpheus.config import Config


class NotFoundError(Exception):
    """Raised when a resource is not found."""

    def __init__(self, resource_type: str, identifier: str):
        self.resource_type = resource_type
        self.identifier = identifier
        super().__init__(f"{resource_type} '{identifier}' not found")


class APIError(Exception):
    """Raised when an API request fails."""

    def __init__(self, message: str):
        super().__init__(message)


class OrpheusClient:
    """Orpheus API client."""

    def __init__(self, config: Config | None = None):
        self.config = config or load_config()
        self._client = httpx.Client(base_url=str(self.base_url), timeout=300)

    @property
    def base_url(self) -> HttpUrl:
        """Base URL for the Orpheus API."""
        return self.config.base_url

    @property
    def user_access_token(self) -> str | None:
        """User access token for the Orpheus API."""
        return self.config.user_access_token

    @property
    def _headers(self) -> dict[str, str]:
        """Headers for API requests."""
        headers = {}
        if self.user_access_token:
            headers["Authorization"] = f"Bearer {self.user_access_token}"
        return headers

    def create_task(
        self,
        spec: str,
        repo_full_name: str,
        snapshot_id: str | None = None,
        program_filter: list[str] | None = None,
        git_branch: str | None = None,
    ) -> dict[str, Any]:
        """Create a new task."""
        body: dict[str, Any] = {"spec": spec, "repo_full_name": repo_full_name}
        if snapshot_id:
            body["snapshot_id"] = snapshot_id
        if program_filter:
            body["program_filter"] = program_filter
        if git_branch:
            body["git_branch"] = git_branch
        response = self._client.post("/api/tasks", json=body, headers=self._headers)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

    def get_task(self, task_slug: str) -> dict[str, Any]:
        """Get task details by slug."""
        response = self._client.get(f"/api/tasks/{task_slug}", headers=self._headers)
        if response.status_code == 404:
            raise NotFoundError("Task", task_slug)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

    def stop_task(self, task_slug: str) -> dict[str, Any]:
        """Stop a task by slug."""
        response = self._client.delete(f"/api/tasks/{task_slug}", headers=self._headers)
        if response.status_code == 404:
            raise NotFoundError("Task", task_slug)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

    def list_tasks(self, active_only: bool = True) -> list[dict[str, Any]]:
        """List tasks."""
        params = {} if active_only else {"active_only": "false"}
        response = self._client.get("/api/tasks", headers=self._headers, params=params)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()["tasks"]

    def get_execution_diff(self, execution_slug: str) -> str:
        """Get diff for an execution by slug."""
        response = self._client.get(f"/api/executions/{execution_slug}/diff", headers=self._headers)
        if response.status_code == 404:
            raise NotFoundError("Execution", execution_slug)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()["diff"]

    def get_execution_ssh(self, execution_slug: str, agent: str | None = None) -> dict[str, Any]:
        """Get SSH credentials for an execution's VM."""
        params = {}
        if agent:
            params["agent"] = agent
        response = self._client.get(f"/api/executions/{execution_slug}/ssh", headers=self._headers, params=params)
        if response.status_code == 404:
            raise NotFoundError("Execution", execution_slug)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

    def list_repos(self) -> list[dict[str, Any]]:
        """List accessible repositories."""
        response = self._client.get("/api/repos", headers=self._headers)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()["repos"]

    def list_programs(self) -> list[dict[str, Any]]:
        """List user's registered programs."""
        response = self._client.get("/api/user/programs", headers=self._headers)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()["programs"]

    def add_program(self, yaml: str, label: str | None = None) -> dict[str, Any]:
        """Register a YAML program spec."""
        body: dict[str, Any] = {"yaml": yaml}
        if label:
            body["label"] = label
        response = self._client.post("/api/user/programs", json=body, headers=self._headers)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

    def remove_program(self, spec_hash: str) -> dict[str, Any]:
        """Unregister a program by its content hash."""
        response = self._client.delete(f"/api/user/programs/{spec_hash}", headers=self._headers)
        if response.status_code == 404:
            raise NotFoundError("Program", spec_hash)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

    def download_file(
        self,
        path: str,
        repo: str | None = None,
        execution_slug: str | None = None,
        agent_name: str | None = None,
    ) -> httpx.Response:
        """Download a file from a VM. Returns the raw response for streaming."""
        params: dict[str, str] = {"path": path}
        if repo:
            params["repo"] = repo
        if execution_slug:
            params["execution_slug"] = execution_slug
        if agent_name:
            params["agent_name"] = agent_name
        response = self._client.get("/api/files/download", params=params, headers=self._headers)
        if response.status_code == 404:
            raise NotFoundError("File", path)
        if response.status_code != 200:
            raise APIError(response.text)
        return response

    def upload_file(
        self,
        local_path: Path | None,
        remote_path: str,
        content: bytes | None = None,
        repo: str | None = None,
        execution_slug: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        """Upload a file to a VM."""
        params: dict[str, str] = {"path": remote_path}
        if repo:
            params["repo"] = repo
        if execution_slug:
            params["execution_slug"] = execution_slug
        if agent_name:
            params["agent_name"] = agent_name

        if content is not None:
            file_data = content
            filename = Path(remote_path).name
        elif local_path:
            file_data = local_path.read_bytes()
            filename = local_path.name
        else:
            raise ValueError("Either local_path or content must be provided")

        response = self._client.post(
            "/api/files/upload",
            params=params,
            files={"file": (filename, file_data)},
            headers=self._headers,
        )
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()
