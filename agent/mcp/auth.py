"""Credential resolution and bounded OAuth refresh for MCP transports."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from agent.protocols.mcp import McpOAuthSpec, McpServerSpec


class McpAuthError(RuntimeError):
    """Credential or token refresh failure."""

    code = "MCP_TOKEN_REFRESH_FAILED"


@dataclass
class _TokenState:
    access_token: str
    expires_at: float | None


class McpCredentialManager:
    """Keep refreshed access tokens in process memory only."""

    def __init__(self) -> None:
        self._tokens: dict[str, _TokenState] = {}

    async def headers_for(self, spec: McpServerSpec) -> dict[str, str]:
        """Return configured headers plus a current OAuth Bearer token."""

        headers = dict(spec.headers)
        if spec.oauth is None:
            return headers
        token = await self._access_token(spec.server_id, spec.oauth, spec.connect_timeout_seconds)
        headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _access_token(
        self,
        server_id: str,
        oauth: McpOAuthSpec,
        timeout: float,
    ) -> str:
        state = self._tokens.get(server_id)
        if state is None:
            state = _TokenState(oauth.access_token, oauth.expires_at)
            self._tokens[server_id] = state
        if state.access_token and (state.expires_at is None or state.expires_at > time.time() + 30):
            return state.access_token
        if not oauth.refresh_token:
            raise McpAuthError("MCP access token is missing or expired")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": oauth.refresh_token,
        }
        if oauth.client_id:
            payload["client_id"] = oauth.client_id
        if oauth.client_secret:
            payload["client_secret"] = oauth.client_secret
        if oauth.scope:
            payload["scope"] = oauth.scope
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(oauth.token_url, data=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise McpAuthError("MCP OAuth token refresh failed") from exc
        access_token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(access_token, str) or not access_token:
            raise McpAuthError("MCP OAuth response did not contain access_token")
        expires_in = body.get("expires_in")
        expires_at = None
        if isinstance(expires_in, int | float) and expires_in > 0:
            expires_at = time.time() + float(expires_in)
        self._tokens[server_id] = _TokenState(access_token, expires_at)
        return access_token
