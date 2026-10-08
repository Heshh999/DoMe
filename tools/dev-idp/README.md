# tools/dev-idp — development-only OpenID Connect issuer

The DoMe backend is a standard OIDC relying party (Authorization Code + PKCE). In production it
talks to Auth0, Keycloak or any compliant issuer. On a developer machine and in integration tests
it talks to **this** tiny issuer instead, so the backend's real authentication code path is
exercised and the backend never needs a "dev login" bypass.

- Binds to `127.0.0.1:8081` by default; signs ID tokens with an RSA key generated at start-up.
- Sign-in page offers `alice@example.test` / `bob@example.test` or any typed email.
- Non-interactive path for tests: `GET /authorize?...&dev_user=alice@example.test` returns the
  redirect immediately.
- Supports discovery, JWKS, PKCE S256, `client_secret_basic`/`client_secret_post`, userinfo.

Run: `uv sync && uv run dome-dev-idp` (or `make dev-idp` from the repository root).

This package is not deployed anywhere, is not a dependency of `cloud-api`, and has no
production mode. Do not add one.
