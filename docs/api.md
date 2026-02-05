# API Reference

EVC Team Relay provides a REST API for authentication, share management, and administration.

## Base URL

```
https://cp.yourdomain.com
```

## Authentication

Most endpoints require a JWT bearer token:

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" https://cp.example.com/v1/shares
```

### Obtain Token

```bash
curl -X POST https://cp.example.com/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "password"}'
```

Response:
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer"
}
```

## Endpoints

### Health & Info

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/health` | No | Health check |
| GET | `/version` | No | API version |
| GET | `/server/info` | No | Server information |
| GET | `/keys/public` | No | Ed25519 public key (PEM) |

### Authentication

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/v1/auth/register` | No | Register new user |
| POST | `/v1/auth/login` | No | Login, get JWT |
| POST | `/v1/auth/logout` | Yes | Invalidate token |
| GET | `/v1/auth/me` | Yes | Current user info |
| PUT | `/v1/auth/me` | Yes | Update profile |
| POST | `/v1/auth/password` | Yes | Change password |

### Shares

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/v1/shares` | Yes | List user's shares |
| POST | `/v1/shares` | Yes | Create share |
| GET | `/v1/shares/{id}` | Yes | Get share details |
| PUT | `/v1/shares/{id}` | Yes | Update share |
| DELETE | `/v1/shares/{id}` | Yes | Delete share |
| GET | `/v1/shares/{id}/members` | Yes | List share members |
| POST | `/v1/shares/{id}/members` | Yes | Add member |
| DELETE | `/v1/shares/{id}/members/{user_id}` | Yes | Remove member |

### Relay Tokens

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/v1/tokens/relay` | Yes | Get relay connection token |

### Admin - Users

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/v1/admin/users` | Admin | List all users |
| POST | `/v1/admin/users` | Admin | Create user |
| GET | `/v1/admin/users/{id}` | Admin | Get user |
| PATCH | `/v1/admin/users/{id}` | Admin | Update user |
| DELETE | `/v1/admin/users/{id}` | Admin | Delete user |

### Admin - Audit Logs

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/v1/admin/audit-logs` | Admin | List audit logs |

### Admin - Settings

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/v1/admin/settings` | Admin | Get all settings |
| GET | `/v1/admin/settings/oauth` | Admin | Get OAuth config |
| PUT | `/v1/admin/settings/oauth` | Admin | Update OAuth config |
| GET | `/v1/admin/settings/branding` | Admin | Get branding |
| PUT | `/v1/admin/settings/branding` | Admin | Update branding |

### Webhooks

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/v1/webhooks` | Yes | List webhooks |
| POST | `/v1/webhooks` | Yes | Create webhook |
| GET | `/v1/webhooks/{id}` | Yes | Get webhook |
| PUT | `/v1/webhooks/{id}` | Yes | Update webhook |
| DELETE | `/v1/webhooks/{id}` | Yes | Delete webhook |
| POST | `/v1/webhooks/{id}/test` | Yes | Test webhook |

## Common Schemas

### Share

```json
{
  "id": "uuid",
  "kind": "doc|folder",
  "path": "/path/to/document.md",
  "visibility": "private|protected|public",
  "owner_id": "uuid",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z",
  "settings": {
    "web_publish_enabled": false,
    "web_publish_slug": null
  }
}
```

### User

```json
{
  "id": "uuid",
  "email": "user@example.com",
  "display_name": "User Name",
  "is_admin": false,
  "is_active": true,
  "created_at": "2024-01-15T10:30:00Z"
}
```

### ShareMember

```json
{
  "user_id": "uuid",
  "share_id": "uuid",
  "role": "viewer|editor",
  "added_at": "2024-01-15T10:30:00Z",
  "added_by": "uuid"
}
```

## Error Responses

All errors follow this format:

```json
{
  "detail": "Error message"
}
```

### HTTP Status Codes

| Code | Meaning |
|------|---------|
| 400 | Bad Request — Invalid input |
| 401 | Unauthorized — Missing or invalid token |
| 403 | Forbidden — Insufficient permissions |
| 404 | Not Found — Resource doesn't exist |
| 409 | Conflict — Resource already exists |
| 422 | Validation Error — Invalid data format |
| 429 | Too Many Requests — Rate limit exceeded |
| 500 | Internal Error — Server error |

## Rate Limits

| Endpoint | Limit |
|----------|-------|
| `/v1/auth/login` | 10/minute |
| `/v1/shares` (POST) | 20/minute |
| `/v1/shares/*/members` (POST) | 30/minute |
| `/v1/tokens/relay` | 30/minute |

When rate limited, response includes:

```json
{
  "detail": "Rate limit exceeded. Try again in 60 seconds."
}
```

## Examples

### Create a Share

```bash
curl -X POST https://cp.example.com/v1/shares \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "kind": "doc",
    "path": "/Notes/Project.md",
    "visibility": "private"
  }'
```

### Add Member to Share

```bash
curl -X POST https://cp.example.com/v1/shares/SHARE_ID/members \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "collaborator@example.com",
    "role": "editor"
  }'
```

### Get Relay Token

```bash
curl -X POST https://cp.example.com/v1/tokens/relay \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "share_id": "SHARE_ID"
  }'
```

Response:
```json
{
  "token": "eyJ...",
  "relay_url": "wss://relay.example.com",
  "expires_at": "2024-01-15T12:30:00Z"
}
```

## OpenAPI Specification

Interactive API documentation is available at:

```
https://cp.yourdomain.com/docs      # Swagger UI
https://cp.yourdomain.com/redoc     # ReDoc
https://cp.yourdomain.com/openapi.json  # OpenAPI spec
```
