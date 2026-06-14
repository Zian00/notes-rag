# Phase 1 — Authentication: Design Spec

**Date:** 2026-06-14
**Status:** Approved (pending written-spec review)
**Phase:** 1 of 5 (see Phase 0 spec roadmap: `docs/superpowers/specs/2026-06-13-foundation-design.md`)

---

## 1. Context

Phase 0 delivered a feature-free FastAPI skeleton: typed `Settings` (with an unused
`jwt_secret`), async SQLAlchemy + Alembic (pgvector enabled), a generic `BaseRepository`, a
health endpoint, and Docker/CI tooling. Phase 1 adds the first real domain: **user accounts and
JWT authentication**, plus the `user_id` scoping seam that every later phase (documents, chat
threads) depends on.

Architecture continues the layered + hexagonal OOP style: Boundary (`api/`) → Control
(`services/`) → Entity (`models/` + `db/repositories/`), with hashing and JWT behind injectable
adapter classes.

### Locked decisions

| Topic | Decision |
|-------|----------|
| Login identifier | Email + password |
| Token strategy | Access token + refresh token |
| Transport | **Hybrid**: access token in JSON body (sent as `Authorization: Bearer`), refresh token in an httpOnly cookie |
| Password hashing | argon2id (`argon2-cffi`) |
| Refresh tokens | DB-stored (hashed), revocable, rotated on each use, with reuse detection |
| Registration | Open self-registration |
| Email verification | Skipped (and password reset deferred) |
| Roles | Single implicit "user" role; data scoped by `user_id` |
| Primary keys | UUID (users + refresh_tokens) |
| JWT library | PyJWT |

---

## 2. Goal & Definition of Done

A user can register, log in, stay logged in via refresh-token rotation, call a protected
endpoint, and log out — all tested.

**Definition of done:**

- `POST /auth/register` creates a user (argon2id-hashed password); duplicate email → 409.
- `POST /auth/login` returns an access token (JSON) and sets a refresh-token httpOnly cookie.
- `GET /auth/me` returns the current user when given a valid `Authorization: Bearer <access>`;
  401 otherwise.
- `POST /auth/refresh` rotates tokens (new access + new refresh cookie, old refresh revoked);
  reuse of a revoked refresh token revokes all of that user's tokens and returns 401.
- `POST /auth/logout` revokes the current refresh token and clears the cookie.
- Alembic migration `0002` creates `users` and `refresh_tokens`.
- All new code TDD'd; `pytest`, `ruff`, `mypy` all green; CI passes.

---

## 3. Components & File Structure

New/modified files:

| Path | Responsibility |
|------|----------------|
| `backend/app/models/__init__.py` | Import models so their tables register on `Base.metadata` |
| `backend/app/models/user.py` | `User` ORM model |
| `backend/app/models/refresh_token.py` | `RefreshToken` ORM model |
| `backend/app/db/repositories/base.py` (modify) | Generalize `get()` key type for UUID PKs |
| `backend/app/db/repositories/user.py` | `UserRepository(BaseRepository)` — `get_by_email` |
| `backend/app/db/repositories/refresh_token.py` | `RefreshTokenRepository(BaseRepository)` — `get_by_hash`, `revoke`, `revoke_all_for_user` |
| `backend/app/core/security.py` | `PasswordHasher` (argon2id) + `TokenService` (JWT encode/decode, opaque refresh generation/hash) |
| `backend/app/schemas/auth.py` | `RegisterRequest`, `LoginRequest`, `TokenResponse`, `UserResponse` |
| `backend/app/services/auth.py` | `AuthService` — orchestrates registration, authentication, rotation, logout |
| `backend/app/api/auth.py` | Auth router (register/login/refresh/logout/me) |
| `backend/app/api/deps.py` | `get_current_user` dependency |
| `backend/app/core/config.py` (modify) | Add JWT/cookie settings |
| `backend/app/db/migrations/versions/0002_*.py` | Create `users` + `refresh_tokens` |
| `backend/app/main.py` (modify) | Register the auth router |
| `backend/tests/...` | Unit + integration tests for all of the above |

Layering: handlers in `api/` only parse requests, call `AuthService`, and shape responses
(including setting/clearing the cookie). Business rules live in `AuthService`. CRUD lives in the
repositories. Hashing/JWT live in `core/security.py` adapter classes, injected into `AuthService`.

---

## 4. Data Model

### `users`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | PK, default generated |
| `email` | str | unique, stored lowercase, indexed |
| `hashed_password` | str | argon2id hash |
| `is_active` | bool | default `true` |
| `created_at` | timestamptz | default now |
| `updated_at` | timestamptz | default now, on update now |

### `refresh_tokens`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | PK |
| `user_id` | UUID | FK → `users.id`, indexed, `ON DELETE CASCADE` |
| `token_hash` | str | SHA-256 of the opaque token, unique, indexed |
| `expires_at` | timestamptz | |
| `revoked_at` | timestamptz | nullable; non-null = revoked |
| `created_at` | timestamptz | default now |

A refresh token is valid iff it exists, `revoked_at IS NULL`, and `expires_at > now()`.

### Retention / cleanup (refresh tokens are NOT kept permanently)
Rows must be bounded. A revoked row is kept until `expires_at` because **reuse detection needs
to find it** (a replayed-after-rotation token must be discoverable as revoked). Once past
`expires_at` it is rejected as expired anyway and is safe to delete.

- **Opportunistic cleanup (Phase 1):** on each login and refresh, delete the current user's
  rows where `expires_at < now()`. No scheduler, keeps the table bounded (≤ refresh lifetime per
  token).
- A periodic background purge job is a documented future enhancement, not built now.

---

## 5. Token Mechanics

- **Access token** — stateless JWT, HS256 signed with `jwt_secret`. Claims: `sub` (user id as
  str), `exp`, `iat`, `type="access"`. Lifetime `access_token_expire_minutes` (default 15).
- **Refresh token** — opaque random string via `secrets.token_urlsafe(32)`. Only its SHA-256
  hash is stored (`refresh_tokens.token_hash`); the raw value is returned only in the httpOnly
  cookie. Lifetime `refresh_token_expire_days` (default 7).
- **Rotation** — on `/auth/refresh`: look up by hash → validate → revoke the old row
  (`revoked_at = now()`) → insert a new refresh token → issue a new access token → set the new
  refresh cookie.
- **Reuse detection** — if the presented token's row exists but is already revoked, treat as
  theft: revoke all of that user's refresh tokens and return 401.

### Cookie
- Name `refresh_token`; `HttpOnly`; `SameSite=Lax`; `Secure` from `cookie_secure` (false in dev
  over http, true in prod); `Path=/auth`; `Max-Age` = refresh lifetime. Cleared on logout.
- **CSRF:** `SameSite=Lax` plus POST-only refresh/logout mitigates the realistic CSRF vectors
  for now. Double-submit CSRF tokens are deferred and noted as a future hardening step.

---

## 6. Endpoints

| Method/Path | Auth | Request | Success | Errors |
|-------------|------|---------|---------|--------|
| `POST /auth/register` | none | `RegisterRequest{email, password}` | 201 `UserResponse` | 409 duplicate email; 422 validation |
| `POST /auth/login` | none | `LoginRequest{email, password}` | 200 `TokenResponse{access_token, token_type}` + Set-Cookie refresh | 401 invalid credentials |
| `POST /auth/refresh` | refresh cookie | — | 200 `TokenResponse` + new Set-Cookie | 401 missing/invalid/expired/revoked (reuse → revoke all) |
| `POST /auth/logout` | refresh cookie | — | 200 | 200 even if no/at-rest cookie (idempotent); clears cookie |
| `GET /auth/me` | Bearer access | — | 200 `UserResponse` | 401 missing/invalid/expired token; inactive user |

Schemas:
- `RegisterRequest`: `email: EmailStr`, `password: str` (min length 8).
- `LoginRequest`: `email: EmailStr`, `password: str`.
- `TokenResponse`: `access_token: str`, `token_type: "bearer"`.
- `UserResponse`: `id: UUID`, `email: str`, `is_active: bool`, `created_at: datetime`.

`get_current_user` dependency: extract Bearer token → decode/validate JWT (signature, exp,
`type=access`) → load user by `sub` → ensure `is_active` → return `User`; else 401. Provides the
`user_id` used for per-user scoping in later phases.

---

## 7. Error Handling

- Handlers validate request shape (Pydantic) and translate service exceptions to HTTP codes.
- `AuthService` raises domain errors (e.g. `EmailAlreadyExists`, `InvalidCredentials`,
  `InvalidRefreshToken`); the router maps them to 409/401.
- Login failures return a generic 401 ("invalid email or password") — never reveal which field
  was wrong.
- Let DB/connection errors bubble (consistent with project conventions).

---

## 8. Configuration Additions (`Settings`)

| Field | Default | Purpose |
|-------|---------|---------|
| `jwt_algorithm` | `"HS256"` | JWT signing alg |
| `access_token_expire_minutes` | `15` | Access token lifetime |
| `refresh_token_expire_days` | `7` | Refresh token lifetime |
| `cookie_secure` | `False` | `Secure` flag on refresh cookie (true in prod) |
| `cookie_samesite` | `"lax"` | `SameSite` policy on refresh cookie |

`jwt_secret` already exists. `.env.example` updated with the new keys (placeholders/defaults).

---

## 9. Dependencies

Add to `backend/pyproject.toml`: `argon2-cffi`, `pyjwt`, `email-validator` (for Pydantic
`EmailStr`). Re-lock with `uv`.

---

## 10. Testing Strategy (TDD)

- **`PasswordHasher`** — hash differs from plaintext; `verify` true for correct, false for wrong;
  re-hashing the same password yields different hashes (salt).
- **`TokenService`** — access JWT encodes/decodes round-trip; expired token rejected; bad
  signature rejected; wrong `type` rejected; opaque refresh + hash are stable/verifiable.
- **`AuthService`** — register creates+hashes and rejects duplicate email; authenticate accepts
  correct creds and rejects wrong; refresh rotates (old revoked, new valid); reuse of a revoked
  token revokes all and errors; logout revokes; expired rows for the user are purged on
  login/refresh (opportunistic cleanup).
- **Repositories** — `UserRepository.get_by_email`; `RefreshTokenRepository.get_by_hash`,
  `revoke`, `revoke_all_for_user`. Integration against the real test DB.
- **Endpoints** — register 201 + duplicate 409; login 200 + sets cookie; `/me` 200 with token,
  401 without/with bad token; refresh rotates and sets a new cookie; reuse → 401; logout clears
  cookie. Use the existing async `client` fixture; add a helper to register+login a test user.

---

## 11. Out of Scope (Phase 1)

Email verification, password reset, OAuth/social login, admin/roles, rate limiting, account
lockout, CSRF double-submit tokens, multi-device session management UI. (Some noted as future
hardening.) Documents, ingestion, RAG, and the frontend belong to later phases.

---

## 12. Risks / Notes

- **UUID PKs** require generalizing `BaseRepository.get` (currently typed `int`); done as part of
  this phase (also resolves a Phase 0 review note).
- **Timestamps stored in UTC, displayed local.** All timestamps use timezone-aware UTC
  (`datetime.now(tz=UTC)`) and Postgres `timestamptz` columns. We do NOT store UTC+8 / SGT.
  Converting to the user's zone (Asia/Singapore, UTC+8) happens only at display time in the
  frontend (Phase 4). This keeps expiry math unambiguous and JWT `exp` (Unix/UTC) consistent
  with DB `expires_at`, and avoids DST/region bugs.
- **Cookie path `/auth`** means the refresh cookie is only sent to auth routes — intentional.
- **Frontend (Phase 4)** will keep the access token in memory and rely on the refresh cookie;
  the XSS/CSRF trade-offs are documented here for that work.
