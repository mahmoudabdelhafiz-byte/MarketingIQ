from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketingiq.domain.models import User

password_hash = PasswordHash.recommended()
TOKEN_ISSUER = "marketingiq-internal"
TOKEN_AUDIENCE = "marketingiq-api"


def hash_password(password: str) -> str:
    return password_hash.hash(password)


_dummy_hash: str | None = None


def _timing_equalizer_hash() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = password_hash.hash("marketingiq-timing-equalizer")
    return _dummy_hash


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    # Always run one Argon2 verification so response time does not reveal whether an
    # account exists or is inactive.
    stored = user.password_hash if user is not None else _timing_equalizer_hash()
    valid = password_hash.verify(password, stored)
    if user is None or not user.is_active or not valid:
        return None
    return user


def create_access_token(user: User, secret: str, minutes: int = 30) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user.id,
            "iat": now,
            "exp": now + timedelta(minutes=minutes),
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
        },
        secret,
        algorithm="HS256",
    )


def decode_access_token(token: str, secret: str) -> str:
    payload = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        issuer=TOKEN_ISSUER,
        audience=TOKEN_AUDIENCE,
        options={"require": ["sub", "iat", "exp", "iss", "aud"]},
    )
    return str(payload["sub"])
