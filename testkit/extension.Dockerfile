# syntax=docker/dockerfile:1
# Builds browser-extension/dist without Node.js on your PC. Used by scripts/start-agent.ps1:
#   docker build -f testkit/extension.Dockerfile --output type=local,dest=<dir> .
# (build context = repository root; the extension depends on shared/ts and shared/protocol).
ARG NODE_IMAGE=node:22-bookworm-slim

FROM ${NODE_IMAGE} AS build
ARG PNPM_VERSION=10.28.0
ENV CI=true
RUN npm install -g "pnpm@${PNPM_VERSION}"
WORKDIR /src
COPY shared/ts/package.json shared/ts/pnpm-lock.yaml shared/ts/
COPY browser-extension/package.json browser-extension/pnpm-lock.yaml browser-extension/
WORKDIR /src/shared/ts
RUN pnpm install --frozen-lockfile
WORKDIR /src/browser-extension
RUN pnpm install --frozen-lockfile
WORKDIR /src
COPY shared/protocol shared/protocol
COPY shared/ts shared/ts
COPY browser-extension browser-extension
WORKDIR /src/browser-extension
RUN pnpm build

FROM scratch
COPY --from=build /src/browser-extension/dist /
