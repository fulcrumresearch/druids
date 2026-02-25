"""GitHub device flow authentication."""

import time

import httpx
from pydantic import BaseModel, HttpUrl

from orpheus.config import get_config


class AuthError(Exception):
    """Authentication failed."""

    pass


class DeviceCodeResponse(BaseModel):
    device_code: str
    user_code: str
    verification_uri: HttpUrl
    expires_in: int
    interval: int


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str
    scope: str


class ErrorResponse(BaseModel):
    error: str
    error_description: str | None = None
    interval: int | None = None

    def check(self) -> int:
        """Return interval for retryable errors, raise AuthError for fatal."""
        match self.error:
            case "authorization_pending":
                return self.interval or 5
            case "slow_down":
                return self.interval or 10
            case "expired_token":
                raise AuthError("Device code expired. Please try again.")
            case "access_denied":
                raise AuthError("Authorization denied by user.")
            case _:
                raise AuthError(f"Unexpected error: {self.error}")


TokenResponse = AccessTokenResponse | ErrorResponse


def parse_token_response(data: dict) -> TokenResponse:
    """Parse GitHub's token endpoint response."""
    if "access_token" in data:
        return AccessTokenResponse.model_validate(data)
    return ErrorResponse.model_validate(data)


def request_device_code() -> DeviceCodeResponse:
    """Request a device code from GitHub."""
    config = get_config()
    response = httpx.post(
        "https://github.com/login/device/code",
        data={"client_id": config.github_client_id},
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    return DeviceCodeResponse.model_validate(response.json())


def poll_for_user_access_token(device_code: str, interval: int) -> str:
    """Poll GitHub until user authorizes, then return user access token."""
    config = get_config()

    while True:
        response = httpx.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": config.github_client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()

        match parse_token_response(response.json()):
            case AccessTokenResponse(access_token=token):
                return token
            case ErrorResponse() as error:
                interval = error.check()
                time.sleep(interval)
