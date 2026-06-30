"""Host-side model-API proxy for sandboxed ramure agents.

Routes an agent's LLM-provider traffic through the ramure host so the agent's
container needs no direct internet access and never holds real provider keys:

    agent (pi, in container) --scoped token--> ModelProxy (host) --real key--> provider

The agent's pi extension overrides the provider base URLs to point here
(``$RAMURE_PROXY_URL/anthropic`` and ``$RAMURE_PROXY_URL/openai/v1``), and the
container's ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` are set to a per-execution
*scoped token* rather than the real key. This proxy validates that token, swaps
in the real provider key from the host environment, forwards to the real
provider, and streams the response back (including SSE).

Dependency-free: stdlib ``http.server`` (a daemon thread, isolated from the
asyncio runtime) for the listener and ``http.client`` for upstream streaming.
"""

from __future__ import annotations

import http.client
import http.server
import os
import threading
from typing import Mapping, Optional

# Headers that must not be copied verbatim when relaying a request/response.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host",
}

# Route prefix -> (default upstream host, credential header, host-env key name).
# Anthropic authenticates with ``x-api-key``; OpenAI with ``Authorization: Bearer``.
_ROUTES = {
    "anthropic": ("api.anthropic.com", "x-api-key", "ANTHROPIC_API_KEY"),
    "openai": ("api.openai.com", "authorization", "OPENAI_API_KEY"),
}


def load_provider_keys(env: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """Read the real provider keys from the host environment (names per ``_ROUTES``)."""
    env = env if env is not None else os.environ
    keys: dict[str, str] = {}
    for route, (_host, _hdr, key_name) in _ROUTES.items():
        val = env.get(key_name)
        if val:
            keys[route] = val
    return keys


class ModelProxy:
    """Threaded HTTP reverse proxy that swaps a scoped token for real provider keys.

    ``keys`` maps a route name (``"anthropic"``/``"openai"``) to the real API key.
    ``upstreams`` optionally overrides the upstream host per route (e.g. to point
    at a gateway). ``token`` is the per-execution scoped token the agent presents.
    """

    def __init__(
        self,
        *,
        token: str,
        keys: Mapping[str, str],
        host: str = "127.0.0.1",
        upstreams: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.token = token
        self.keys = dict(keys)
        self.host = host
        self.upstreams = {r: (upstreams or {}).get(r, default) for r, (default, _h, _k) in _ROUTES.items()}
        self.hits: dict[str, int] = {r: 0 for r in _ROUTES}  # forwarded requests per route
        self._server: Optional[http.server.ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> Optional[int]:
        return self._server.server_address[1] if self._server else None

    @property
    def base_url(self) -> Optional[str]:
        return f"http://{self.host}:{self.port}" if self._server else None

    def start(self) -> str:
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # silence default logging
                pass

            def do_POST(self):
                proxy._handle(self)

            def do_GET(self):
                proxy._handle(self)

        self._server = http.server.ThreadingHTTPServer((self.host, 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="ramure-model-proxy", daemon=True
        )
        self._thread.start()
        return self.base_url  # type: ignore[return-value]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            self._thread = None

    # -- request handling (runs on the daemon-thread server) --

    def _route(self, path: str):
        """Return (route_name, upstream_host, cred_header, real_key, forward_path) or None."""
        for route, (_default, cred_header, _key_name) in _ROUTES.items():
            prefix = "/" + route
            if path == prefix or path.startswith(prefix + "/"):
                forward_path = path[len(prefix):] or "/"
                return route, self.upstreams[route], cred_header, self.keys.get(route), forward_path
        return None

    def _handle(self, h: http.server.BaseHTTPRequestHandler) -> None:
        routed = self._route(h.path)
        if routed is None:
            h.send_error(404, "unknown proxy route")
            return
        route, upstream, cred_header, real_key, forward_path = routed
        if not real_key:
            h.send_error(503, f"proxy has no upstream key for {route}")
            return

        # Validate the scoped token the agent presented (x-api-key: <tok> or Bearer <tok>).
        presented = h.headers.get(cred_header, "") or ""
        if self.token not in presented:
            h.send_error(401, "invalid proxy token")
            return

        length = int(h.headers.get("content-length", 0) or 0)
        body = h.rfile.read(length) if length else b""

        self.hits[route] = self.hits.get(route, 0) + 1
        if os.environ.get("RAMURE_PROXY_DEBUG"):
            import sys
            print(f"[proxy] {h.command} {h.path} -> https://{upstream}{forward_path}", file=sys.stderr, flush=True)
            print(f"[proxy]   in-headers: {dict(h.headers.items())}", file=sys.stderr, flush=True)
        # Drop hop-by-hop headers AND the client's credential header (case-insensitively)
        # so we don't end up sending two differently-cased auth headers (which trips a
        # Cloudflare 400). Then set exactly one real credential, and one Host.
        out_headers = {
            k: v for k, v in h.headers.items()
            if k.lower() not in _HOP_BY_HOP and k.lower() != cred_header
        }
        if cred_header == "x-api-key":
            out_headers["x-api-key"] = real_key
        else:
            out_headers["Authorization"] = f"Bearer {real_key}"
        out_headers["Host"] = upstream

        conn = None
        try:
            conn = http.client.HTTPSConnection(upstream, timeout=900)
            conn.request(h.command, forward_path, body=body, headers=out_headers)
            resp = conn.getresponse()
        except Exception as e:  # upstream/connection failure -> 502, never crash the thread
            try:
                h.send_error(502, f"upstream error: {type(e).__name__}")
            except Exception:
                pass
            if conn is not None:
                conn.close()
            return

        if os.environ.get("RAMURE_PROXY_DEBUG"):
            import sys
            ct = resp.getheader("content-type")
            print(f"[proxy]   <- {resp.status} {ct}", file=sys.stderr, flush=True)
        # Relay status + headers, then stream the body (SSE included). We drop the
        # upstream framing headers and close the connection so the client reads to EOF.
        h.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() in _HOP_BY_HOP:
                continue
            h.send_header(k, v)
        h.send_header("Connection", "close")
        h.end_headers()
        try:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                h.wfile.write(chunk)
                h.wfile.flush()
        except Exception:
            pass
        finally:
            conn.close()
