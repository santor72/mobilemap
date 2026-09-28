import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import Settings, get_settings

security = HTTPBasic()


def require_basic_auth(
    credentials: HTTPBasicCredentials = Depends(security),
    settings: Settings = Depends(get_settings),
) -> str:
    if not settings.auth_username or not settings.auth_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AUTH_NOT_CONFIGURED", "error": "Auth is not configured"},
        )

    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        settings.auth_username.encode("utf-8"),
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        settings.auth_password.encode("utf-8"),
    )
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "error": "Invalid credentials"},
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
