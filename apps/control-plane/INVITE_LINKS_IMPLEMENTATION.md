# Invite Links Feature Implementation Summary

## Overview

Successfully implemented Phase 1 (Backend Core) of the Invite Links feature for relay-onprem control plane based on spec v1.3-invite-links.md.

## Implementation Date
2026-01-26

## Files Created

### 1. Database Migration
- **File**: `app/db/migrations/versions/202601260001_add_share_invites.py`
- **Purpose**: Creates `share_invites` table and adds new audit action enums
- **Key Features**:
  - UUID primary key
  - 64-character secure token (indexed, unique)
  - Role assignment (viewer/editor)
  - Optional expiration (indexed for query performance)
  - Optional usage limits with atomic counter
  - Revocation support via timestamp
  - Cascading delete on share/user deletion

### 2. Database Model
- **File**: `app/db/models.py` (modified)
- **Changes**:
  - Added `ShareInvite` model with all required fields
  - Added relationship to `Share` model
  - Added new audit actions: `INVITE_CREATED`, `INVITE_REVOKED`, `INVITE_REDEEMED`

### 3. Pydantic Schemas
- **File**: `app/schemas/invite.py`
- **Schemas**:
  - `InviteCreate`: Create invite with role, expiration, max uses
  - `InviteRead`: Full invite details (for owners)
  - `InvitePublicInfo`: Public info for unauthenticated users
  - `InviteRedeemNewUser`: Registration data for new users
  - `InviteRedeemResponse`: Response after redemption

### 4. Service Layer
- **File**: `app/services/invite_service.py`
- **Functions**:
  - `generate_secure_token()`: 32-byte cryptographically secure token
  - `create_invite()`: Create invite with audit logging
  - `get_invite_by_token()`: Retrieve invite with relationships
  - `list_invites()`: List all invites for a share
  - `revoke_invite()`: Soft-delete via revoked_at timestamp
  - `validate_invite()`: Check expiration, revocation, usage limits
  - `get_invite_public_info()`: Public endpoint (no auth)
  - `redeem_invite()`: Handle both new user registration and existing user joins

### 5. API Router
- **File**: `app/api/routers/invites.py`
- **Endpoints**:

  **Authenticated (Owner/Admin only):**
  - `POST /shares/{share_id}/invites` - Create invite (rate limited: 10/min)
  - `GET /shares/{share_id}/invites` - List invites for share
  - `DELETE /shares/{share_id}/invites/{invite_id}` - Revoke invite

  **Public (No auth required):**
  - `GET /invite/{token}` - View invite details
  - `POST /invite/{token}/redeem` - Redeem invite (rate limited: 10/min)

### 6. Tests
- **File**: `tests/test_invites.py`
- **Coverage**: 21 comprehensive tests covering:
  - Invite creation (owner permissions, defaults)
  - Invite listing (authorization checks)
  - Invite revocation (soft delete)
  - Public info retrieval (valid/invalid/revoked)
  - Redemption flows (existing user, new user, edge cases)
  - Idempotency (duplicate redemption)
  - Usage limits enforcement
  - Owner cannot join own share
  - Audit logging

### 7. Configuration Updates
- **File**: `app/main.py` (modified)
  - Registered `invites.router` for authenticated endpoints
  - Registered `invites.public_router` for public endpoints
  - Added "invites" to API documentation tags
- **File**: `tests/conftest.py` (modified)
  - Added `invites.limiter` to rate limiter reset list

## Key Features Implemented

### Security
- ✅ Cryptographically secure 64-char tokens (32 bytes hex = 256-bit entropy)
- ✅ Rate limiting: 10/min on create and redeem
- ✅ Owner cannot join own share
- ✅ Authorization checks (owner/admin only for management)
- ✅ Timezone-aware datetime handling (compatible with SQLite and PostgreSQL)

### Functionality
- ✅ Multiple active invites per share
- ✅ Role assignment (viewer/editor)
- ✅ Optional expiration (1-30 days, or unlimited)
- ✅ Optional usage limits with atomic counter
- ✅ Soft deletion via revocation timestamp
- ✅ Public invite info endpoint (no auth)
- ✅ New user auto-registration via invite
- ✅ Existing user auto-join via invite
- ✅ Idempotent redemption (no error if already member)

### Audit Logging
- ✅ `INVITE_CREATED`: Tracks who created invite, with details
- ✅ `INVITE_REVOKED`: Tracks who revoked invite
- ✅ `INVITE_REDEEMED`: Tracks who redeemed, whether new user

### Error Handling
- ✅ 404 for invalid tokens
- ✅ 410 for expired/revoked/usage-limited invites
- ✅ 400 for owner trying to join own share
- ✅ 400 for duplicate email registration
- ✅ 403 for unauthorized access to management endpoints

## Testing Results

All 21 tests pass:
```
TestInviteCreation: 4/4 passed
TestInviteListing: 2/2 passed
TestInviteRevocation: 2/2 passed
TestInvitePublicInfo: 3/3 passed
TestInviteRedemption: 8/8 passed
TestInviteAuditLogs: 2/2 passed
```

## Database Schema

```sql
CREATE TABLE share_invites (
    id UUID PRIMARY KEY,
    share_id UUID NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
    token VARCHAR(64) NOT NULL UNIQUE,
    created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('viewer', 'editor')),
    expires_at TIMESTAMP WITH TIME ZONE,
    max_uses INTEGER,
    use_count INTEGER NOT NULL DEFAULT 0,
    revoked_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_share_invites_token ON share_invites(token);
CREATE INDEX idx_share_invites_share_id ON share_invites(share_id);
CREATE INDEX idx_share_invites_expires_at ON share_invites(expires_at);
```

## API Examples

### Create Invite
```bash
POST /shares/{share_id}/invites
Authorization: Bearer <jwt-token>
{
  "role": "editor",
  "expires_in_days": 7,
  "max_uses": 10
}
```

### Get Public Invite Info
```bash
GET /invite/{token}
# No auth required
```

### Redeem as New User
```bash
POST /invite/{token}/redeem
{
  "email": "newuser@example.com",
  "password": "securepass123"
}
# Returns: user info + share info + access_token
```

### Redeem as Existing User
```bash
POST /invite/{token}/redeem
Authorization: Bearer <jwt-token>
# No body needed
# Returns: user info + share info
```

## Next Steps

### Phase 2: Public Invite Page (Not Implemented)
- SSR template for `/invite/{token}` page
- Sign-up and login forms
- Success/error pages
- Styling to match admin UI

### Phase 3: Plugin Enhancement (Not Implemented)
- Add invite link generation to ShareManagementModal
- Display active invites with usage stats
- Copy-to-clipboard functionality
- Revoke button for each invite

## Deployment Notes

1. **Migration Required**: Run `alembic upgrade head` to create `share_invites` table
2. **No Breaking Changes**: All endpoints are new, no existing functionality affected
3. **Rate Limiting**: Uses existing slowapi infrastructure
4. **Database Compatibility**: Works with both PostgreSQL (production) and SQLite (tests)

## Code Quality

- ✅ All tests passing (21/21)
- ✅ Ruff formatting applied
- ✅ Ruff linting passed
- ✅ Type hints throughout
- ✅ Comprehensive docstrings
- ✅ Follows existing codebase patterns

## Related Specification

Implementation based on:
- **Spec**: `/Users/rogozhin/DevProjects/relay-onprem/docs/specs/v1.3-invite-links.md`
- **Version**: 1.0
- **Date**: 2026-01-26
