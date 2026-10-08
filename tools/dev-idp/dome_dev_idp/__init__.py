"""Development-only OpenID Connect issuer.

This process stands in for Auth0/Keycloak on a developer machine and in integration tests so
that the DoMe backend exercises its real OIDC relying-party code path. It has no production
mode, binds to loopback by default, and signs tokens with a key generated at start-up.
"""

from .app import DevIdpSettings, create_app

__all__ = ["DevIdpSettings", "create_app"]
