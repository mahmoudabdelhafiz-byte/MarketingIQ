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


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    if user is None or not user.is_active or not password_hash.verify(password, user.password_hash):
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
