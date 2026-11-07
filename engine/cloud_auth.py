"""JWT boundary with configured trust roots and an uncached session check.

No discovery URL, key, algorithm or database role is accepted from a client.
Organization authorization belongs to fresh PostgreSQL membership/RLS checks.
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping
from uuid import UUID

import jwt


class AuthError(ValueError):
    """The credential cannot authorize this request (fail closed)."""


@dataclass(frozen=True)
class VerifiedIdentity:
    user_id: UUID
    session_id: UUID


@dataclass(frozen=True)
class TrustedKey:
    algorithm: str
    key: Any

    @classmethod
    def from_jwk(cls, value: Mapping[str, Any]) -> "TrustedKey":
        """Load a public JWK obtained through an operator-controlled channel.

        This method does not fetch a URL. Symmetric JWKs and private keys are
        deliberately excluded from a public JWKS configuration.
        """
        algorithm = value.get("alg")
        if algorithm not in {"RS256", "ES256"}:
            raise ValueError("Only RS256 and ES256 public JWKs are accepted")
        if "d" in value or value.get("use", "sig") != "sig":
            raise ValueError("Expected a public signature key")
        if algorithm == "RS256" and value.get("kty") != "RSA":
            raise ValueError("RSA key required")
        if algorithm == "ES256" and (value.get("kty"), value.get("crv")) != ("EC", "P-256"):
            raise ValueError("P-256 key required")
        key = jwt.PyJWK.from_dict(dict(value), algorithm=algorithm).key
        if algorithm == "RS256" and key.key_size < 2048:
            raise ValueError("RSA key must have at least 2048 bits")
        return cls(algorithm, key)


class AuthVerifier:
    def __init__(self, *, issuer: str, audience: str = "authenticated",
                 keys: Mapping[str, TrustedKey],
                 session_checker: Callable[[UUID, UUID], bool],
                 leeway_seconds: int = 0):
        if not issuer or not audience or not keys or not callable(session_checker):
            raise ValueError("Issuer, audience, trusted keys and session checker are required")
        if not 0 <= leeway_seconds <= 30:
            raise ValueError("JWT clock leeway must be between 0 and 30 seconds")
        for kid, key in keys.items():
            if not isinstance(kid, str) or not isinstance(key, TrustedKey):
                raise ValueError("Invalid trusted key map")
            if key.algorithm not in {"HS256", "ES256", "RS256"}:
                raise ValueError("Unsupported trusted algorithm")
            if key.algorithm == "HS256":
                if not isinstance(key.key, (str, bytes)) or len(key.key) < 32:
                    raise ValueError("HS256 requires at least 32 bytes of secret")
        self.issuer, self.audience = issuer, audience
        self.keys = MappingProxyType(dict(keys))
        self.session_checker = session_checker
        self.leeway_seconds = leeway_seconds

    def verify(self, bearer_token: str) -> VerifiedIdentity:
        """Verify a raw token; the HTTP boundary parses the Bearer header.

        Session liveness is intentionally checked on every request, including
        retries. Callback errors cannot fall back to trusting the signed JWT.
        """
        try:
            if not isinstance(bearer_token, str) or not 1 <= len(bearer_token) <= 16384:
                raise AuthError("Invalid credential")
            header = jwt.get_unverified_header(bearer_token)
            if (header.get("typ") != "JWT" or header.get("crit") or
                    any(k in header for k in ("jku", "x5u", "jwk", "x5c", "zip"))):
                raise AuthError("Invalid credential")
            kid = header.get("kid", "")
            if not isinstance(kid, str) or kid not in self.keys:
                raise AuthError("Invalid credential")
            key = self.keys[kid]
            if header.get("alg") != key.algorithm:
                raise AuthError("Invalid credential")
            claims = jwt.decode(
                bearer_token, key.key, algorithms=[key.algorithm],
                issuer=self.issuer, audience=self.audience,
                leeway=self.leeway_seconds,
                options={"require": ["iss", "aud", "exp", "iat", "sub", "session_id", "role"],
                         "strict_aud": True},
            )
            if claims["role"] != "authenticated" or claims.get("is_anonymous") is not False:
                raise AuthError("Invalid credential")
            if any(type(claims[k]) not in (int, float) for k in ("iat", "exp")):
                raise AuthError("Invalid credential")
            if claims["exp"] <= claims["iat"]:
                raise AuthError("Invalid credential")
            identity = VerifiedIdentity(UUID(claims["sub"]), UUID(claims["session_id"]))
            if self.session_checker(identity.user_id, identity.session_id) is not True:
                raise AuthError("Invalid credential")
            return identity
        except AuthError:
            raise
        except Exception as exc:
            # Deliberately excludes token contents and provider errors from the
            # public message; callers should record only the rejection category.
            raise AuthError("Invalid credential") from exc


class PostgresSessionChecker:
    """Use a restricted login granted only the g3a_session_reader role.

    auth.sessions is official GoTrue state, not a fixture or client claim.
    not_after is checked because expired sessions can remain during cleanup.
    """
    def __init__(self, dsn: str):
        self.dsn = dsn

    def __call__(self, user_id: UUID, session_id: UUID) -> bool:
        import psycopg
        with psycopg.connect(self.dsn, connect_timeout=5) as db:
            db.execute("SET LOCAL ROLE g3a_session_reader")
            db.execute("SET LOCAL statement_timeout = '3000ms'")
            db.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(user_id),))
            return db.execute(
                "SELECT EXISTS (SELECT 1 FROM auth.sessions WHERE id = %s "
                "AND user_id = %s AND (not_after IS NULL OR not_after > now()))",
                (session_id, user_id),
            ).fetchone()[0]
