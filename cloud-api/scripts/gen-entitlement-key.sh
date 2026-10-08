#!/usr/bin/env sh
# Generate the development EdDSA (Ed25519) entitlement signing key.
#
#   ./scripts/gen-entitlement-key.sh            -> $DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH or ./.dome-dev-state/entitlement-ed25519.pem
#   ./scripts/gen-entitlement-key.sh path.pem   -> explicit path
#   ./scripts/gen-entitlement-key.sh --force    -> replace an existing file
#
# The file is written with mode 0600 and must stay out of version control. Production deployments
# take the key from their secret store instead (see README.md "Entitlement signing key").
set -eu
cd "$(dirname "$0")/.."
exec uv run dome-api-gen-entitlement-key "$@"
