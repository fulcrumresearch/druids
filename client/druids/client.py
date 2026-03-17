"""HTTP client for Druids server API."""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

import httpx

from druids.config import load_config


if TYPE_CHECKING:
    from druids.config import Config


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


class DruidsClient:
    """Druids API client."""

    def __init__(self, config: Config | None = None):
        self.config = config or load_config()
        self._client = httpx.Client(base_url=str(self.base_url), timeout=300)

    @property
    def base_url(self) -> str:
        """Base URL for the Druids API."""
        return self.config.base_url

    @property
    def user_access_token(self) -> str | None:
        """User access token for the Druids API."""
        return self.config.user_access_token

    @property
    def _headers(self) -> dict[str, str]:
        """Headers for API requests."""
        headers = {}
        if self.user_access_token:
            headers["Authorization"] = f"Bearer {self.user_access_token}"
        return headers

    def create_execution(
        self,
        program_source: str,
        repo_full_name: str | None = None,
        devbox_name: str | None = None,
        args: dict[str, str] | None = None,
        git_branch: str | None = None,
        ttl: int = 0,
        files: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a new execution from program source."""
        body: dict[str, Any] = {"program_source": program_source}
        if repo_full_name:
            body["repo_full_name"] = repo_full_name
        if devbox_name:
            body["devbox_name"] = devbox_name
        if args:
            body["args"] = args
        if git_branch:
            body["git_branch"] = git_branch
        if ttl > 0:
            body["ttl"] = ttl
        if files:
            body["files"] = files
        response = self._client.post("/api/executions", json=body, headers=self._headers)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

    def get_execution(self, slug: str) -> dict[str, Any]:
        """Get execution details by slug."""
        response = self._client.get(f"/api/executions/{slug}", headers=self._headers)
        if response.status_code == 404:
            raise NotFoundError("Execution", slug)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

    def stream_execution(self, slug: str) -> Generator[dict[str, Any], None, None]:
        """Stream execution trace events via SSE. Yields parsed event dicts.

        Connects to the SSE stream and yields each activity event as a dict.
        Stops when the server sends an `event: done` or the connection closes.
        """
        stream_timeout = httpx.Timeout(connect=30, read=None, write=30, pool=30)
        with self._client.stream(
            "GET",
            f"/api/executions/{slug}/stream",
            headers=self._headers,
            timeout=stream_timeout,
        ) as response:
            if response.status_code == 404:
                raise NotFoundError("Execution", slug)
            if response.status_code != 200:
                raise APIError(f"Stream failed: {response.status_code}")

            event_type = ""
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event_type = line[len("event:") :].strip()
                    if event_type == "done":
                        return
                elif line.startswith("data:"):
                    data_str = line[len("data:") :].strip()
                    if event_type == "activity" and data_str:
                        yield json.loads(data_str)
                    event_type = ""

    def stop_execution(self, slug: str) -> dict[str, Any]:
        """Stop an execution by slug."""
        response = self._client.patch(
            f"/api/executions/{slug}",
            json={"status": "stopped"},
            headers=self._headers,
        )
        if response.status_code == 404:
            raise NotFoundError("Execution", slug)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

    def list_executions(self, active_only: bool = True) -> list[dict[str, Any]]:
        """List executions."""
        params = {} if active_only else {"active_only": "false"}
        response = self._client.get("/api/executions", headers=self._headers, params=params)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()["executions"]

    def list_tools(self, execution_slug: str, agent_name: str) -> list[str]:
        """List tools registered for an agent."""
        response = self._client.get(
            f"/api/executions/{execution_slug}/agents/{agent_name}/tools",
            headers=self._headers,
        )
        if response.status_code == 404:
            raise NotFoundError("Agent", agent_name)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()["tools"]

    def call_tool(self, execution_slug: str, agent_name: str, tool_name: str, args: dict) -> Any:
        """Call a tool registered for an agent."""
        response = self._client.post(
            f"/api/executions/{execution_slug}/agents/{agent_name}/tools/{tool_name}",
            json={"args": args},
            headers=self._headers,
        )
        if response.status_code == 404:
            raise NotFoundError("Tool", tool_name)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()["result"]

    def setup_start(
        self,
        name: str | None = None,
        repo_full_name: str | None = None,
        public: bool = False,
    ) -> dict[str, Any]:
        """Start devbox setup: provision sandbox and return SSH credentials."""
        body: dict[str, Any] = {}
        if name:
            body["name"] = name
        if repo_full_name:
            body["repo_full_name"] = repo_full_name
        if public:
            body["public"] = True
        response = self._client.post("/api/devbox/setup/start", json=body, headers=self._headers)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

    def setup_finish(
        self,
        name: str | None = None,
        repo_full_name: str | None = None,
    ) -> dict[str, Any]:
        """Finish devbox setup: snapshot and stop the sandbox."""
        body: dict[str, Any] = {}
        if name:
            body["name"] = name
        if repo_full_name:
            body["repo_full_name"] = repo_full_name
        response = self._client.post("/api/devbox/setup/finish", json=body, headers=self._headers)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

    def list_devboxes(self) -> list[dict[str, Any]]:
        """List all devboxes for the current user."""
        response = self._client.get("/api/devboxes", headers=self._headers)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()["devboxes"]

    def get_execution_activity(self, slug: str, n: int = 50, compact: bool = True) -> dict[str, Any]:
        """Get recent activity for an execution."""
        params = {"n": str(n), "compact": str(compact).lower()}
        response = self._client.get(f"/api/executions/{slug}/activity", headers=self._headers, params=params)
        if response.status_code == 404:
            raise NotFoundError("Execution", slug)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

    def get_execution_diff(self, execution_slug: str) -> str:
        """Get diff for an execution by slug."""
        response = self._client.get(f"/api/executions/{execution_slug}/diff", headers=self._headers)
        if response.status_code == 404:
            raise NotFoundError("Execution", execution_slug)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()["diff"]

    def send_agent_message(self, execution_slug: str, agent_name: str, text: str) -> dict[str, Any]:
        """Send a chat message to an agent in a running execution."""
        response = self._client.post(
            f"/api/executions/{execution_slug}/agents/{agent_name}/message",
            json={"text": text},
            headers=self._headers,
        )
        if response.status_code == 404:
            raise NotFoundError("Agent", agent_name)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

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

    def set_secrets(
        self,
        secrets: dict[str, str],
        devbox_name: str | None = None,
        repo_full_name: str | None = None,
    ) -> dict[str, Any]:
        """Set one or more secrets on a devbox."""
        body: dict[str, Any] = {"secrets": secrets}
        if devbox_name:
            body["devbox_name"] = devbox_name
        if repo_full_name:
            body["repo_full_name"] = repo_full_name
        response = self._client.post("/api/secrets", json=body, headers=self._headers)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()

    def list_secrets(
        self,
        devbox_name: str | None = None,
        repo_full_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """List secret names for a devbox."""
        params: dict[str, str] = {}
        if devbox_name:
            params["devbox_name"] = devbox_name
        if repo_full_name:
            params["repo_full_name"] = repo_full_name
        response = self._client.get("/api/secrets", params=params, headers=self._headers)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()["secrets"]

    def delete_secret(
        self,
        name: str,
        devbox_name: str | None = None,
        repo_full_name: str | None = None,
    ) -> dict[str, Any]:
        """Delete a secret from a devbox."""
        body: dict[str, Any] = {"name": name}
        if devbox_name:
            body["devbox_name"] = devbox_name
        if repo_full_name:
            body["repo_full_name"] = repo_full_name
        response = self._client.request("DELETE", "/api/secrets", json=body, headers=self._headers)
        if response.status_code != 200:
            raise APIError(response.text)
        return response.json()
