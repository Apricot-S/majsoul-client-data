# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12
ARG OS_VERSION=trixie
FROM python:${PYTHON_VERSION}-slim-${OS_VERSION} AS base

FROM base AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV UV_PYTHON_DOWNLOADS=never

WORKDIR /opt/app
COPY pyproject.toml uv.lock README.md LICENSE THIRD-PARTY-NOTICES.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable \
        --python /usr/local/bin/python --link-mode copy

FROM base AS runner

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/app/.venv/bin:$PATH"

ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home /nonexistent \
    --shell /usr/sbin/nologin \
    --no-create-home \
    --uid "${UID}" \
    appuser

COPY --from=builder /opt/app/.venv /opt/app/.venv

WORKDIR /work
USER appuser
ENTRYPOINT ["majsoul-client-data"]
CMD ["--help"]
