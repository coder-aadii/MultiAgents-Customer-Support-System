from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import structlog
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings

logger = structlog.get_logger()


class CustomerAuthError(Exception):
    """Base exception for customer authentication failures."""


class CustomerTokenAlgorithmError(CustomerAuthError):
    """Rejected algorithm in token header (e.g. none or disallowed algorithm)."""


class CustomerTokenExpiredError(CustomerAuthError):
    """Customer token has expired."""


class CustomerTokenClaimsError(CustomerAuthError):
    """Token missing required claims or invalid claim values (iss, aud, sub)."""


class CustomerTokenInvalidError(CustomerAuthError):
    """Malformed token, signature verification failure, or decoding error."""


class VerifiedCustomerIdentity(BaseModel):
    """Cryptographically verified customer identity representation (IAL 2)."""

    customer_id: str = Field(description="Host system customer subject ID")
    email: str | None = Field(default=None, description="Verified customer email if present")
    issuer: str = Field(description="Configured token issuer")
    audience: str = Field(description="Configured token audience")
    identity_assurance_level: int = Field(
        default=2, description="Identity assurance level (2 = verified)"
    )
    token_hash: str = Field(description="SHA-256 digest of verified token")
    expires_at: datetime = Field(description="Token expiration timestamp")
    issued_at: datetime = Field(description="Token issuance timestamp")
    organization_id: str | None = Field(default=None, description="Optional tenant organization ID claim")

    model_config = ConfigDict(frozen=True)


class CustomerTokenVerifier:
    """Zero-trust verifier for external customer identity tokens.

    Enforces:
    - Explicitly configured algorithm whitelist (rejects 'none' and dynamic header algorithm confusion).
    - Minimum key length (256-bit for symmetric HMAC).
    - Strict mandatory claim validation (sub, iss, aud, exp, iat).
    - Safe logging: never logs raw tokens or private credentials.
    """

    def __init__(
        self,
        secret: str | None = None,
        public_key: str | None = None,
        allowed_algorithms: list[str] | None = None,
        issuer: str | None = None,
        audience: str | None = None,
    ) -> None:
        self.secret = secret if secret is not None else settings.CUSTOMER_JWT_SECRET
        self.public_key = public_key if public_key is not None else settings.CUSTOMER_JWT_PUBLIC_KEY
        self.allowed_algorithms = [
            alg.strip()
            for alg in (
                allowed_algorithms
                if allowed_algorithms is not None
                else settings.CUSTOMER_JWT_ALGORITHMS
            )
            if alg.strip()
        ]
        self.issuer = issuer if issuer is not None else settings.CUSTOMER_JWT_ISSUER
        self.audience = audience if audience is not None else settings.CUSTOMER_JWT_AUDIENCE

        # Fail fast on insecure configuration
        for alg in self.allowed_algorithms:
            if alg.lower() == "none":
                raise CustomerTokenAlgorithmError("Algorithm 'none' is explicitly prohibited")

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def verify_token(self, token: str) -> VerifiedCustomerIdentity:
        """Verify customer token against strict security invariants.

        Raises CustomerAuthError subclasses on failure.
        Never logs or exposes the raw token.
        """
        if not token or not isinstance(token, str):
            raise CustomerTokenInvalidError("Customer token is missing or malformed")

        token_stripped = token.strip()
        if not token_stripped:
            raise CustomerTokenInvalidError("Customer token cannot be empty")

        # 1. Inspect unverified header for algorithm safety
        try:
            unverified_header = jwt.get_unverified_header(token_stripped)
        except Exception as e:
            raise CustomerTokenInvalidError("Malformed JWT header") from e

        alg = unverified_header.get("alg")
        if not alg or not isinstance(alg, str):
            raise CustomerTokenAlgorithmError("Token header missing algorithm")

        alg_upper = alg.upper()
        if alg.lower() == "none" or alg_upper == "NONE":
            raise CustomerTokenAlgorithmError("Algorithm 'none' is strictly rejected")

        if alg_upper not in self.allowed_algorithms:
            raise CustomerTokenAlgorithmError(
                f"Algorithm '{alg}' is not in configured allowed algorithms: {self.allowed_algorithms}"
            )

        # 2. Select appropriate verification key
        verification_key: str
        if alg_upper in ("RS256", "ES256"):
            if not self.public_key:
                raise CustomerTokenAlgorithmError(
                    f"Public key not configured for asymmetric algorithm {alg_upper}"
                )
            verification_key = self.public_key
        else:
            # HS256 / HMAC
            if len(self.secret) < 32:
                raise CustomerTokenAlgorithmError("HMAC secret key must be at least 32 characters (256 bits)")
            verification_key = self.secret

        # 3. Cryptographically decode and validate claims
        try:
            payload = jwt.decode(
                token_stripped,
                verification_key,
                algorithms=[alg_upper],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "require": ["sub", "iss", "aud", "exp", "iat"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except jwt.ExpiredSignatureError as e:
            raise CustomerTokenExpiredError("Customer token has expired") from e
        except jwt.InvalidIssuerError as e:
            raise CustomerTokenClaimsError("Token issuer mismatch") from e
        except jwt.InvalidAudienceError as e:
            raise CustomerTokenClaimsError("Token audience mismatch") from e
        except jwt.InvalidSignatureError as e:
            raise CustomerTokenInvalidError("Invalid token signature") from e
        except jwt.MissingRequiredClaimError as e:
            raise CustomerTokenClaimsError(f"Missing required claim: {e}") from e
        except jwt.PyJWTError as e:
            raise CustomerTokenInvalidError(f"Token validation failed: {e}") from e

        # 4. Validate subject claim
        sub = payload.get("sub")
        if not sub or not isinstance(sub, str) or not sub.strip():
            raise CustomerTokenClaimsError("Subject claim 'sub' must be a non-empty string")

        email = payload.get("email")
        if email is not None and not isinstance(email, str):
            email = None

        exp_timestamp = payload.get("exp")
        iat_timestamp = payload.get("iat")

        expires_at = (
            datetime.fromtimestamp(exp_timestamp, tz=UTC)
            if exp_timestamp
            else datetime.now(UTC) + timedelta(hours=1)
        )
        issued_at = (
            datetime.fromtimestamp(iat_timestamp, tz=UTC)
            if iat_timestamp
            else datetime.now(UTC)
        )

        org_claim = payload.get("org_id") or payload.get("organization_id")
        org_id_str = str(org_claim).strip() if org_claim is not None else None

        return VerifiedCustomerIdentity(
            customer_id=sub.strip(),
            email=email.strip() if email else None,
            issuer=str(payload.get("iss")),
            audience=str(payload.get("aud")),
            identity_assurance_level=2,
            token_hash=self._hash_token(token_stripped),
            expires_at=expires_at,
            issued_at=issued_at,
            organization_id=org_id_str,
        )


def create_customer_jwt(
    customer_id: str,
    email: str | None = None,
    secret: str | None = None,
    private_key: str | None = None,
    algorithm: str = "HS256",
    issuer: str | None = None,
    audience: str | None = None,
    expires_in_minutes: int = 60,
    issued_at: datetime | None = None,
    custom_claims: dict[str, Any] | None = None,
) -> str:
    """Generate a signed customer JWT for testing or host portal simulation."""
    now = issued_at or datetime.now(UTC)
    expires_at = now + timedelta(minutes=expires_in_minutes)

    payload: dict[str, Any] = {
        "sub": customer_id,
        "iss": issuer or settings.CUSTOMER_JWT_ISSUER,
        "aud": audience or settings.CUSTOMER_JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if email:
        payload["email"] = email

    if custom_claims:
        payload.update(custom_claims)

    if algorithm.upper() in ("RS256", "ES256"):
        if not private_key:
            raise ValueError("Private key required for asymmetric signing")
        return jwt.encode(payload, private_key, algorithm=algorithm.upper())

    signing_key = secret or settings.CUSTOMER_JWT_SECRET
    return jwt.encode(payload, signing_key, algorithm=algorithm.upper())

