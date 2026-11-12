# tools/dev-idp — development-only OpenID Connect issuer

The DoMe backend is a standard OIDC relying party (Authorization Code + PKCE). In production it
talks to Auth0, Keycloak or any compliant issuer. On a developer machine and in integration tests
it talks to **this** tiny issuer instead, so the backend's real authentication code path is
exercised and the backend never needs a "dev login" bypass.

- Binds to `127.0.0.1:8081` by default; signs ID tokens with an RSA key generated at start-up.
- Sign-in page offers `alice@example.test` / `bob@example.test` or any typed email.
- Non-interactive path for tests: `GET /authorize?...&dev_user=alice@example.test` returns the
  redirect immediately.
- ID tokens say `email_verified: true` unless a test asks otherwise: `&dev_email_verified=false` or
  `&dev_email_verified=absent` (claim left out) on the `dev_user` path, or the sign-in page's checkbox
  "Treat this email as not confirmed yet". DoMe refuses both (`cloud-api/DECISIONS.md` #118).
- Supports discovery, JWKS, PKCE S256, `client_secret_basic`/`client_secret_post`, userinfo.
- Accepts `prompt=login`, which DoMe sends on sign-ins after a refused one (`cloud-api/DECISIONS.md`
  #119). It keeps no sign-in session, so its page is shown every time anyway; the `dev_user` path
  still answers at once.
- Every ID token carries `auth_time`, the moment of that sign-in. DoMe asks for it (`max_age=0`)
  before deleting an account and checks it (`cloud-api/DECISIONS.md` #121).

Run: `uv sync && uv run dome-dev-idp` (or `make dev-idp` from the repository root).

This package is not deployed anywhere, is not a dependency of `cloud-api`, and has no
production mode. Do not add one.
