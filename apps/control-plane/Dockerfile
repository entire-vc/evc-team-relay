FROM python:3.12-slim

# Pin the uv binary itself (not just what it resolves) so the build tool is
# reproducible too, not only the Python deps it locks in. Installed from PyPI
# (not ghcr.io/astral-sh/uv) so the build has no dependency on GitHub's
# availability — see task c3fa1c20 (live GitHub egress block broke this).
RUN pip install --no-cache-dir --root-user-action=ignore uv==0.12.5

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY uv.lock pyproject.toml /app/
COPY app /app/app
COPY alembic.ini /app/alembic.ini

# --locked fails the build if uv.lock is out of sync with pyproject.toml
# instead of silently re-resolving; --no-dev excludes the [dependency-groups]
# dev group (pytest/pytest-asyncio) from the production image.
RUN uv sync --locked --no-dev

# Non-root runtime user. admin_ui.py writes uploaded favicons/logos to
# app/static/uploads at runtime, so that path needs to be writable by it.
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser && \
    mkdir -p /app/app/static/uploads && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]