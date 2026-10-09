#!/bin/sh
# Container entrypoint for the DoMe cloud-api image (deploy/Dockerfile).
#
# Does two small, explicit things before exec'ing the command, because hosting secret stores
# (Fly.io, most PaaS) only provide environment variables while the API wants a key FILE and a
# SQLAlchemy-style database URL:
#
# 1. DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64 (base64 of the Ed25519 PKCS8 PEM) is written to
#    DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH with mode 0600. In DOME_ENV=development only, a missing
#    key is generated with the repository's own `dome-api-gen-entitlement-key`; staging/production
#    refuse to invent a signing key.
# 2. DATABASE_URL (postgres:// or postgresql://, the form `fly postgres attach` injects) is
#    translated to DOME_DATABASE_URL (postgresql+psycopg://) when DOME_DATABASE_URL is not set.
#
# Nothing here is a login path, a default secret or a fallback that hides a misconfiguration:
# every other setting is still validated by dome_api/settings.py, which stops the process.
set -eu

key_path="${DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH:-/app/run/entitlement-ed25519.pem}"
env_name="${DOME_ENV:-development}"

if [ -n "${DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64:-}" ]; then
    mkdir -p "$(dirname "$key_path")"
    umask 077
    printf '%s' "$DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64" | base64 -d > "$key_path"
    umask 022
    echo "entrypoint: wrote entitlement signing key to $key_path" >&2
elif [ ! -f "$key_path" ]; then
    case "$env_name" in
        development)
            echo "entrypoint: DOME_ENV=development and no key at $key_path; generating a development key" >&2
            dome-api-gen-entitlement-key "$key_path" >&2
            ;;
        *)
            echo "entrypoint: no entitlement signing key at $key_path and DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64 is unset;" >&2
            echo "entrypoint: refusing to generate one in DOME_ENV=$env_name (see deploy/README.md 'Secrets')" >&2
            exit 2
            ;;
    esac
fi

if [ -z "${DOME_DATABASE_URL:-}" ] && [ -n "${DATABASE_URL:-}" ]; then
    case "$DATABASE_URL" in
        postgres://*)   DOME_DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgres://}" ;;
        postgresql://*) DOME_DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgresql://}" ;;
        *)              DOME_DATABASE_URL="$DATABASE_URL" ;;  # let settings.py reject it with a clear message
    esac
    export DOME_DATABASE_URL
    echo "entrypoint: derived DOME_DATABASE_URL from DATABASE_URL" >&2
fi

exec "$@"
