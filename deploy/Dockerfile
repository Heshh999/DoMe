# syntax=docker/dockerfile:1
#
# DoMe cloud-api + relay + the built PWA in one image.
#
# Build context is the REPOSITORY ROOT (the API's `dome-protocol` dependency is the path
# dependency ../shared/python, which in turn packages ../shared/protocol):
#
#   docker build -f deploy/Dockerfile -t dome-api .
#
# Stages:  pwa        Node 22 + pnpm: `pnpm build` of mobile-app (vite)   -> /src/mobile-app/dist
#          api-build  python:3.12 + uv: `uv sync --locked --no-dev`        -> /app/cloud-api/.venv
#          runtime    python:3.12-slim, non-root, venv + PWA + entrypoint; CMD `dome-api`
#
# Evidence: built by CI on every push since 2026-10-11 (.github/workflows/ci.yml, job "container"),
# which also checks the image refuses to invent a signing key outside development and carries the
# build commit. Built and run in the test-kit rehearsal (Linux). NOT yet run on Fly.io.
#
# The Deploy workflow (deploy/launch/dome_deploy.py) builds it with:
#   --build-arg VITE_DOME_RELEASE_CHANNEL=staging|production  --build-arg VITE_DOME_SUPPORT_URL=...
#   --build-arg DOME_BUILD_COMMIT=<40-hex git commit>          (reported by /healthz as "commit")

# Base images are pinned by DIGEST (each tag's multi-platform index), so every build of a commit, staging's
# and production's alike, starts from the same layers; a re-pushed tag changes nothing here. The tag stays for
# readers; with a digest present, docker uses the digest. Resolved 2026-10-14 from Docker Hub and ghcr.io
# (registry HEAD of the tag, Docker-Content-Digest). To update, resolve the tag again (for example
# `docker buildx imagetools inspect python:3.12-slim-bookworm`), replace the digest, and let CI build it.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
# Same uv release that produced the lockfiles in this repository.
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c

############################################################################################
# 1. PWA
############################################################################################
FROM ${NODE_IMAGE} AS pwa
ARG PNPM_VERSION=10.28.0
# Build-time, public values (see .env.example "mobile-app"). Empty = same origin for the API.
ARG VITE_DOME_RELEASE_CHANNEL=staging
ARG VITE_DOME_API_ORIGIN=
ARG VITE_DOME_SUPPORT_URL=
ENV CI=true \
    VITE_DOME_RELEASE_CHANNEL=${VITE_DOME_RELEASE_CHANNEL} \
    VITE_DOME_API_ORIGIN=${VITE_DOME_API_ORIGIN} \
    VITE_DOME_SUPPORT_URL=${VITE_DOME_SUPPORT_URL}
RUN npm install -g "pnpm@${PNPM_VERSION}"
WORKDIR /src
# Manifests and lockfiles first so dependency layers are cached independently of source edits.
# mobile-app depends on @dome/protocol via `link:../shared/ts`, which keeps its own node_modules.
COPY shared/ts/package.json shared/ts/pnpm-lock.yaml shared/ts/
COPY mobile-app/package.json mobile-app/pnpm-lock.yaml mobile-app/
WORKDIR /src/shared/ts
RUN pnpm install --frozen-lockfile
WORKDIR /src/mobile-app
RUN pnpm install --frozen-lockfile
# Sources: the shared TS library reads the frozen contract JSON from ../protocol at build time.
WORKDIR /src
COPY shared/protocol shared/protocol
COPY shared/ts shared/ts
COPY mobile-app mobile-app
WORKDIR /src/mobile-app
RUN pnpm build

############################################################################################
# 2. API dependencies
############################################################################################
FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS api-build
COPY --from=uv /uv /uvx /bin/
# Use the image's interpreter (never a downloaded one) so the venv stays valid in the runtime
# stage, which is built from the same base image.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY shared/protocol shared/protocol
COPY shared/python shared/python
COPY cloud-api/pyproject.toml cloud-api/uv.lock cloud-api/
WORKDIR /app/cloud-api
# --locked: fail if uv.lock is out of date with pyproject.toml instead of silently re-resolving.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY cloud-api /app/cloud-api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

############################################################################################
# 3. Runtime
############################################################################################
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="DoMe cloud-api" \
      org.opencontainers.image.description="DoMe backend, relay and PWA (FastAPI + uvicorn)" \
      org.opencontainers.image.source="https://github.com/heshh999/dome"
RUN groupadd --system --gid 10001 dome \
 && useradd --system --uid 10001 --gid dome --home-dir /app --shell /usr/sbin/nologin dome \
 && mkdir -p /app/run && chown dome:dome /app/run
COPY --from=api-build --chown=dome:dome /app /app
COPY --from=pwa --chown=dome:dome /src/mobile-app/dist /app/static
COPY --chown=dome:dome --chmod=0755 deploy/entrypoint.sh /app/entrypoint.sh
# The git commit this image was built from (empty for local builds). Declared after the COPY lines so a
# new commit does not invalidate the cached layers above; settings.py accepts only 7-40 hex digits.
ARG DOME_BUILD_COMMIT=
LABEL org.opencontainers.image.revision="${DOME_BUILD_COMMIT}"
ENV DOME_BUILD_COMMIT=${DOME_BUILD_COMMIT} \
    PATH="/app/cloud-api/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    DOME_STATIC_DIR=/app/static \
    DOME_API_BIND=0.0.0.0:8080 \
    DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH=/app/run/entitlement-ed25519.pem
# alembic.ini and the migrations are resolved relative to this directory (dome_api/db/migrate.py).
WORKDIR /app/cloud-api
USER dome
EXPOSE 8080
# /healthz answers 200 only when the database round-trip succeeds (503 = degraded).
# The port must match DOME_API_BIND; override both if you change it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4); sys.exit(0 if r.status == 200 else 1)"]
ENTRYPOINT ["/app/entrypoint.sh"]
# `dome-api` runs `alembic upgrade head`, then serves. Fly runs the migration once as a release
# command and starts the process with `--skip-migrations` (see fly.toml).
CMD ["dome-api"]
