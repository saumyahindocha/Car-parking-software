"""Request dependencies: DB session, authentication (users and devices), role checks."""
from __future__ import annotations

from typing import Callable, Iterator, Optional

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal, get_engine
from ..models import Role, User
from ..security import read_token


def get_db() -> Iterator[Session]:
    get_engine()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def current_user(db: Session = Depends(get_db), authorization: Optional[str] = Header(None),
                 token: Optional[str] = Query(None, include_in_schema=False)) -> User:
    claims = read_token(_bearer(authorization) or token or "")
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    user = db.get(User, claims["uid"])
    if user is None or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user disabled")
    if user.device_id and claims.get("dev") and claims["dev"] != user.device_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "device not bound to this user")
    db.info["user_id"] = user.id
    return user


def require(*roles: str) -> Callable[[User], User]:
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role {' or '.join(roles)}")
        return user

    return dep


staff = require(*Role.ALL)
supervisor = require(Role.SUPERVISOR, Role.ADMIN)
admin = require(Role.ADMIN)
collector = require(Role.WORKER, Role.SUPERVISOR, Role.ADMIN)


def device_key(x_device_key: Optional[str] = Header(None), key: Optional[str] = Query(None, include_in_schema=False)) -> str:
    s = get_settings()
    k = x_device_key or key
    if k not in (s.anpr_api_key, s.device_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad device key")
    return k


def anpr_key(x_device_key: Optional[str] = Header(None)) -> str:
    if x_device_key != get_settings().anpr_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad ANPR key")
    return x_device_key


def relay_key(x_relay_key: Optional[str] = Header(None)) -> str:
    if x_relay_key != get_settings().relay_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad relay key")
    return x_relay_key
