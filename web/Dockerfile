# One image for the service's three commands: api, worker, migrate.
# The build context is this directory.

FROM dhi.io/node:26.13.1-alpine3.24-dev AS frontend

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
# From the lockfile, and without running what the packages ask to run on
# install: nothing here needs it, and it is where a poisoned package acts.
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM dhi.io/python:3.15.2-alpine3.24-dev AS backend

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
WORKDIR /backend
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
# Exactly what uv.lock names, into a virtualenv at the path the runtime stage
# copies it to. With the image's own Python, never one uv would download.
ENV UV_PROJECT_ENVIRONMENT=/opt/repwise-web \
    UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_DOWNLOADS=never
RUN uv sync --frozen --no-dev --no-editable --no-cache

# The virtualenv's interpreter is a symlink to the one that created it, which
# the runtime image has at the same path only while both stages are the same
# Python. Dependabot groups the two tags so that they move together.
FROM dhi.io/python:3.15.2-alpine3.24

COPY --from=backend /opt/repwise-web /opt/repwise-web
COPY --from=frontend /frontend/dist /srv/web

# Unbuffered, so that what a run prints reaches the pod's log when it happens.
ENV PATH=/opt/repwise-web/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    STATIC_DIR=/srv/web

ENTRYPOINT ["repwise-web"]
CMD ["api"]
