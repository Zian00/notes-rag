# Phase 1 — Authentication: Concepts

This guide explains the auth system we built and *why* each piece works the way it does.
It's accurate to the code in `backend/app/` (services/security/api/models).

---

## The big picture

A request flows through our layers:

```
HTTP request
   │
   ▼
api/auth.py        ← Boundary: parse request, set/clear cookies, map errors to HTTP codes
   │  (Depends)
   ▼
services/auth.py   ← Control: AuthService — the business logic, owns the DB transaction
   │
   ├─► core/security.py   ← adapters: PasswordHasher (argon2id), TokenService (JWT)
   └─► db/repositories/*  ← Entity persistence: UserRepository, RefreshTokenRepository
```

Handlers contain no business logic; the service never builds raw SQL; hashing and JWT live
behind small adapter classes that are *injected* into the service (`api/deps.py`). That's the
layered + hexagonal style — you could swap argon2 for something else by changing one class.

---

## Password hashing (argon2id)

We never store passwords. On register we store an **argon2id hash**
(`PasswordHasher.hash`), and on login we verify the candidate password against that hash
(`PasswordHasher.verify`).

- **Why hash?** If the DB leaks, raw passwords would be catastrophic. A hash can't be reversed.
- **Why argon2id?** It's memory-hard (resists GPU brute-forcing) and the current OWASP
  recommendation. Each hash embeds a random **salt**, so the same password hashes differently
  every time (`test_same_password_hashes_differently`) — that defeats rainbow tables.
- `verify` returns `False` for a wrong password *or* a corrupt stored hash — it never raises,
  so a bad value can't turn into a 500.

---

## Access tokens (stateless JWT)

After login the client gets a short-lived **JWT access token** (`TokenService.create_access_token`).
It's signed with our secret (`JWT_SECRET`, HS256) and carries claims: `sub` (the user id),
`iat`, `exp`, and `type="access"`.

- **Stateless** = the server doesn't store it. To check a request we just verify the signature
  and expiry (`jwt.decode`) — no DB lookup for the token itself. Fast and scalable.
- `decode_access_token` pins the algorithm (`algorithms=[self._alg]`) — this blocks the classic
  "alg=none" / algorithm-confusion attacks. It also checks `type=="access"` so a refresh-typed
  token can't be used as an access token, and rejects a malformed `sub`.
- **Short-lived (15 min)** on purpose: if it leaks, the damage window is small. That's why we
  also have refresh tokens.

`get_current_user` (`api/deps.py`) is the dependency that protects routes: it pulls the
`Authorization: Bearer <token>`, decodes it, loads the user, and checks `is_active`. Any failure
→ 401. This is also where later phases get `user_id` to scope data per user.

---

## Refresh tokens (opaque, DB-backed, rotating)

Access tokens expire fast, so the client uses a **refresh token** to get new ones without
re-entering a password.

- **Opaque, not a JWT:** the refresh token is just random bytes (`secrets.token_urlsafe(32)`).
- **Only the hash is stored:** we save `sha256(raw)` in `refresh_tokens.token_hash`, never the
  raw value. So a DB leak doesn't hand out usable tokens. The raw value lives *only* in the
  cookie on the client.
- **Rotation:** every call to `/auth/refresh` revokes the presented token and issues a brand-new
  one (`AuthService.refresh` → `revoke(old)` then `issue_tokens`). A refresh token is single-use.
- **Reuse detection:** if someone presents a token that's *already been revoked* (i.e. it was
  rotated away — a sign it was stolen and replayed), we revoke **all** of that user's tokens and
  reject. See `AuthService.refresh`: the revoked-token branch calls `revoke_all_for_user` and
  commits before raising 401.
- **Retention:** tokens aren't kept forever. A revoked row is kept until `expires_at` (needed so
  reuse detection can still find it), then purged. Cleanup is *opportunistic*:
  `issue_tokens` deletes the user's expired rows on every login/refresh
  (`delete_expired_for_user`).

---

## Hybrid transport (Bearer header + httpOnly cookie)

We send the two tokens differently, on purpose:

| Token | Where | Why |
|-------|-------|-----|
| Access | JSON body → client sends `Authorization: Bearer …` | Simple, standard, easy to attach to API calls |
| Refresh | `Set-Cookie` (httpOnly) | JS can't read it, so an XSS script can't steal it |

- **httpOnly** means JavaScript cannot read the cookie — that's the security win for the
  long-lived refresh token. (You won't see it in `document.cookie` or Swagger's header panel;
  that's expected — browsers hide `Set-Cookie` from JS.)
- **CSRF:** because cookies are auto-sent, cookie auth is exposed to CSRF. We mitigate with
  `SameSite=Lax` and POST-only refresh/logout; a fuller double-submit CSRF token is a documented
  future hardening step.
- **`Path=/auth`:** the refresh cookie is only sent to `/auth/*` routes, not every request.
- **`Secure`** is config-driven (`COOKIE_SECURE`) — off in local http dev, on in prod (https).

---

## Why `AuthService` owns the transaction

`AuthService` commits explicitly (it holds the session). The subtle reason: in the reuse-detection
path we must **revoke all tokens AND then return 401**. If the request-teardown rolled back on the
raised exception, that revocation would be undone — exactly the wrong outcome. By committing inside
the service before raising, the security action persists regardless of the HTTP error. Repositories
only `flush`; the service decides when to `commit`.

---

## UUID primary keys & UTC timestamps

- **UUID PKs** (users, refresh_tokens): non-sequential, so IDs don't leak "how many users exist"
  and aren't guessable in URLs. We generalized `BaseRepository.get` to accept any key type for this.
- **UTC everywhere:** timestamps are timezone-aware UTC (`utils/time.utcnow`, `DateTime(timezone=True)`),
  and JWT `exp`/`iat` are UTC too — so expiry comparisons are unambiguous. We convert to local time
  (SGT, UTC+8) only when *displaying* in the frontend (Phase 4), never at rest.

---

## Endpoints recap

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `POST /auth/register` | none | Create account → 201 user (409 if email taken) |
| `POST /auth/login` | none | Verify creds → access token (JSON) + refresh cookie |
| `POST /auth/refresh` | refresh cookie | Rotate → new access + new refresh cookie |
| `POST /auth/logout` | refresh cookie | Revoke refresh token, clear cookie |
| `GET /auth/me` | Bearer access | Return the current user |

---

## How to test it

- Automated: `make test` (unit + integration against the dedicated `notes_rag_test` DB).
- Manual: `make dev`, open `http://localhost:8000/docs`, then register → login → Authorize with
  the access token → `/auth/me` → refresh → logout.
