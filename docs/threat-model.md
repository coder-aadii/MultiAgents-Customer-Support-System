# MCSS Threat Model

## Scope

This threat model covers the MCSS MVP: API service, admin web console, embeddable widget, and PostgreSQL database. It assumes deployment behind a reverse proxy (Caddy) with TLS termination.

## Assets

| Asset | Classification | Description |
|-------|----------------|-------------|
| Visitor PII (name, email) | Confidential | Optional, provided by visitor |
| Conversation content | Confidential | Support chat transcripts |
| Knowledge base documents | Internal | Company documentation |
| Staff credentials | Critical | Argon2id-hashed passwords |
| JWT secrets | Critical | Token signing keys |
| LLM API keys | Critical | Provider credentials |
| Audit logs | Confidential | Security-relevant events |

## Trust Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│                     Internet                                │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Caddy (TLS, WAF)                         │
└──────────────────────────┬──────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │   Widget    │ │  Admin Web  │ │    API      │
    │  (Visitor)  │ │   (Staff)   │ │  (Internal) │
    └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
           │               │               │
           └───────────────┼───────────────┘
                           ▼
                ┌─────────────────────┐
                │   PostgreSQL +      │
                │   pgvector          │
                └─────────────────────┘
```

## Threat Analysis (STRIDE)

### Spoofing

| Threat | Mitigation |
|--------|------------|
| Staff credential theft | Argon2id hashing, rate-limited login (5/15min), generic error messages |
| Visitor session hijacking | Short-lived visitor JWT (24h), token in memory only, Origin validation |
| JWT forgery | Strong secret (32+ chars), RS256 in production, short access tokens (15min) |
| Admin impersonation | Role validation on every request, admin-only routes protected |

### Tampering

| Threat | Mitigation |
|--------|------------|
| Message injection (XSS) | All content rendered as text, no `dangerouslySetInnerHTML`, CSP headers |
| SQL injection | SQLAlchemy ORM with parameterized queries only |
| API parameter tampering | Pydantic validation on all inputs, UUID validation |
| Document content tampering | Draft→Published→Archived workflow, audit events on changes |
| Widget configuration tampering | Origin validation, admin-only settings endpoint |

### Repudiation

| Threat | Mitigation |
|--------|------------|
| Staff denies action | Immutable audit_events table with actor, event_type, resource, timestamp |
| Visitor denies message | Messages tied to visitor token, conversation linkage |
| System denies handoff | Handoff records with reason, timestamp, claimer |

### Information Disclosure

| Threat | Mitigation |
|--------|------------|
| Conversation leakage | Visitor tokens scoped to single conversation, staff see only assigned/unassigned |
| PII in logs | Structured logging excludes message content, passwords, tokens, API keys |
| Database exposure | PostgreSQL not exposed externally, only API container accesses |
| Knowledge base leakage | Only published documents retrievable, visitor cannot list documents |
| Error detail leakage | Generic error messages in production, detailed only in development |

### Denial of Service

| Threat | Mitigation |
|--------|------------|
| Login brute force | Rate limiting (5 attempts/15min per email+IP) |
| Widget spam | Visitor message rate limit (10/min), session create limit (30/hr/origin) |
| Large payloads | Request size limits (JSON 64KB, upload 5MB) |
| Provider exhaustion | Circuit breaker (3 failures/60s → 60s cooldown), timeouts (10s/20s/15s) |
| Database exhaustion | Connection pooling, query timeouts, pagination limits |

### Elevation of Privilege

| Threat | Mitigation |
|--------|------------|
| Staff accesses admin routes | Role check middleware, 403 for staff on admin-only routes |
| Visitor accesses staff API | Separate token types, visitor endpoints validate conversation scope |
| Document delete bypass | Draft-only deletion, published requires archive |
| Provider key extraction | Keys only in env, never logged, fake provider in test |

## Data Flow: Visitor Message Processing

```
Visitor Browser
    │
    ▼ POST /widget/messages (Bearer visitor_token)
Widget validates token → scopes to conversation
    │
    ▼ Persist visitor message
API checks conversation status
    │
    ▼ If staff_active/closed → stop
Conversation Service
    │
    ▼ Classify (LLM, temp=0)
Assistant Service → JSON: answer|clarify|handoff
    │
    ▼ If answer: Retrieve top 5 chunks (cosine ≥ 0.72)
Knowledge Service → published chunks only
    │
    ▼ Generate answer (LLM, structured output)
Assistant Service → JSON with source_ids, confidence
    │
    ▼ Verify citations, confidence ≥ 0.75
Output Verification
    │
    ▼ Persist assistant message + audit
Database
    │
    ▼ Poll → Widget displays
Visitor Browser
```

**Handoff triggers** (any true → immediate handoff):
- Visitor requests human/refund/cancellation/account/payment
- Unsafe domain (legal/medical/financial/security/emergency)
- Retrieval score < 0.72 or no published sources
- Model confidence < 0.75
- Schema/citation verification fails twice
- Provider/embedding/parser/DB failure after message saved

## Security Controls Summary

| Control | Implementation |
|---------|----------------|
| Authentication | JWT (access + refresh), Argon2id, HttpOnly cookies |
| Authorization | Role-based (admin/staff), token-scoped visitor access |
| Encryption | TLS 1.3 via Caddy, passwords Argon2id, secrets in env |
| Input Validation | Pydantic schemas, UUID params, size limits |
| Output Encoding | React auto-escape, no dangerouslySetInnerHTML |
| Rate Limiting | Login, widget sessions, visitor messages |
| Audit Logging | Immutable events for all mutations |
| Headers | CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy |
| Secrets | .env only, never logged, fake provider for CI |
| Dependencies | Pinned versions, regular updates, no dev deps in prod |

## Residual Risks (Accepted for MVP)

1. **No WAF rules beyond Caddy defaults** - Acceptable for small deployment; add custom rules if attacked
2. **WuKongIM delivery with API reconciliation** - the API remains authoritative, and clients re-fetch after reconnecting
3. **No automated conversation retention deletion** - Documented as ops task (30-day default)
4. **Single-tenant only** - By design for MVP; multi-tenant requires schema changes
5. **No backup/restore automation** - Documented in runbook as manual process

## Review Schedule

- Review on each major release
- Update when new features cross trust boundaries
- Annual review minimum
