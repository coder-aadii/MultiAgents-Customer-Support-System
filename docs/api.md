# MCSS API Reference

## Base URL

```
Development: http://localhost:8000/api/v1
Production:  https://support.example.com/api/v1
```

## Authentication

### Staff Endpoints
- Access token: `Authorization: Bearer <access_token>` (15 min)
- Refresh token: HttpOnly cookie (7 days)

### Visitor Endpoints
- Visitor token: `Authorization: Bearer <visitor_token>` (24 hours)
- Scoped to single conversation

## Error Format

All errors follow this structure:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message",
    "details": {}
  }
}
```

Common error codes:
- `VALIDATION_ERROR` - Request validation failed (422)
- `UNAUTHORIZED` - Invalid or missing authentication (401)
- `FORBIDDEN` - Insufficient permissions (403)
- `NOT_FOUND` - Resource not found (404)
- `RATE_LIMITED` - Too many requests (429)
- `INTERNAL_ERROR` - Unexpected server error (500)

## Health Endpoints

### GET /healthz
Liveness probe - no database dependency.

**Response 200:**
```json
{"status": "ok"}
```

### GET /readyz
Readiness probe - verifies database connectivity.

**Response 200:**
```json
{"status": "ok"}
```

**Response 503:**
```json
{"status": "not ready", "reason": "database unavailable"}
```

## Auth Endpoints

### POST /auth/login
Staff login with email/password.

**Request:**
```json
{"email": "staff@example.com", "password": "secret"}
```

**Response 200:**
*(Refresh token set as HttpOnly path-scoped cookie `mcss_refresh_token`)*
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 900,
  "user": {
    "id": "uuid",
    "email": "staff@example.com",
    "display_name": "Staff User",
    "role": "staff"
  }
}
```

### POST /auth/refresh
Rotate refresh token.

**Response 200:**
```json
{"access_token": "eyJ...", "token_type": "bearer"}
```

### POST /auth/logout
Clear refresh token cookie.

**Response 200:**
```json
{"message": "Logged out"}
```

### GET /auth/me
Get current staff user.

**Response 200:**
```json
{
  "id": "uuid",
  "email": "staff@example.com",
  "display_name": "Staff Name",
  "role": "admin|staff",
  "is_active": true,
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

## Widget Endpoints

### POST /widget/sessions
Create or resume visitor session.

**Request:**
```json
{"visitor_external_id": "optional-uuid-from-localstorage"}
```

**Headers:** `Origin: https://example.com` (validated against widget settings)

**Response 200:**
```json
{
  "visitor_id": "uuid",
  "conversation_id": "uuid",
  "token": "visitor-jwt",
  "welcome_message": "Hi! How can MCSS Support help today?",
  "visitor_external_id": "uuid"
}
```

### GET /widget/conversation
Get current conversation.

**Headers:** `Authorization: Bearer <visitor_token>`

**Response 200:**
```json
{
  "id": "uuid",
  "status": "open|waiting_for_staff|staff_active|closed",
  "title": "Optional title",
  "last_message_at": "2024-01-01T00:00:00Z"
}
```

### GET /widget/messages
Get conversation messages (paginated).

**Query:** `?cursor=<message_id>&limit=50`

**Headers:** `Authorization: Bearer <visitor_token>`

**Response 200:**
```json
{
  "messages": [
    {
      "id": "uuid",
      "conversation_id": "uuid",
      "sender_type": "visitor|assistant|staff|system",
      "sender_user_id": "uuid|null",
      "content": "Message text",
      "metadata": {
        "source_ids": ["uuid"],
        "confidence": 0.95,
        "handoff_reason": "low_confidence|visitor_requested|..."
      },
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "next_cursor": "uuid|null"
}
```

### POST /widget/messages
Send visitor message.

**Headers:** `Authorization: Bearer <visitor_token>`

**Request:**
```json
{"content": "Hello, I need help"}
```

**Response 200:**
```json
{"id": "uuid", "created_at": "2024-01-01T00:00:00Z"}
```

### PATCH /widget/profile
Update visitor profile.

**Headers:** `Authorization: Bearer <visitor_token>`

**Request:**
```json
{"name": "John", "email": "john@example.com"}
```

## Admin/Staff Endpoints

### Conversations

#### GET /conversations
List conversations with filters.

**Query:** `?status=open&cursor=<id>&limit=25`

**Response 200:**
```json
{
  "items": [
    {
      "id": "uuid",
      "visitor_id": "uuid",
      "visitor": {
        "id": "uuid",
        "name": "John",
        "email": "john@example.com"
      },
      "status": "open|waiting_for_staff|staff_active|closed",
      "assigned_user_id": "uuid|null",
      "title": "Optional title",
      "last_message_at": "2024-01-01T00:00:00Z",
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-01-01T00:00:00Z"
    }
  ],
  "next_cursor": "<opaque_cursor>|null",
  "has_more": false
}
```

#### GET /conversations/{id}
Get conversation details.

#### GET /conversations/{id}/messages
Get messages (paginated).

#### POST /conversations/{id}/messages
Staff reply to conversation.

**Request:**
```json
{
  "content": "I can help with that",
  "client_message_id": "uuid"
}
```

#### POST /conversations/{id}/claim
Claim conversation (staff_active).

#### POST /conversations/{id}/close
Close conversation.

### Handoffs

#### GET /handoffs
List handoffs.

**Query:** `?status=queued&limit=25`

#### POST /handoffs/{id}/claim
Claim handoff.

### Knowledge Base

#### GET /knowledge/documents
List documents.

#### POST /knowledge/documents
Create document (draft).

**Request:**
```json
{
  "title": "FAQ",
  "source_type": "markdown",
  "source_filename": "faq.md",
  "content": "# FAQ\n\nContent here..."
}
```

#### GET /knowledge/documents/{id}
Get document.

#### PATCH /knowledge/documents/{id}
Update draft document.

#### DELETE /knowledge/documents/{id}
Delete draft document.

#### POST /knowledge/documents/{id}/publish
Publish draft document (guarded: requires ingestion_status=ready and chunks > 0).

#### POST /knowledge/documents/{id}/archive
Archive published document.

### Settings

#### GET /settings/widget
Get widget settings.

#### PUT /settings/widget
Update widget settings (admin only).

**Request:**
```json
{
  "allowed_origin": "https://example.com",
  "welcome_message": "Welcome!",
  "accent_color": "#2563eb"
}
```

### Users (Admin Only)

#### GET /users
List staff users.

#### POST /users
Create staff user.

#### PATCH /users/{id}
Update user (deactivate, role change).

### Audit Events (Admin Only)

#### GET /audit-events
List audit events.

**Query:** `?cursor=<id>&limit=50`

## Rate Limits

- Login: 5 attempts / 15 min / email+IP
- Widget session create: 30 / hour / origin
- Visitor messages: 10 / minute / visitor
- Document upload: 5 MB max

## Security Headers

All responses include:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY` (admin routes)
- `Content-Security-Policy: restrictive`
- `Referrer-Policy: no-referrer`
- `X-Request-ID: <uuid>`