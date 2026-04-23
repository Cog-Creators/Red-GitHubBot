# build stage
FROM python:3.14-trixie AS build

# Install system dependencies required for building
RUN set -x \
    && apt-get update \
    && apt-get install --no-install-recommends -y build-essential git libpq-dev libssl-dev

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

# https://docs.astral.sh/uv/guides/integration/docker/#compiling-bytecode
ENV UV_COMPILE_BYTECODE=1
# https://docs.astral.sh/uv/guides/integration/docker/#caching
ENV UV_LINK_MODE=copy
# https://github.com/astral-sh/uv-docker-example/pull/42
ENV UV_PYTHON_DOWNLOADS=0
# Disable development dependencies
ENV UV_NO_DEV=1
# Install the project as non-editable
ENV UV_NO_EDITABLE=1

WORKDIR /app

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RED_GITHUBBOT=0.1 \
    uv sync --locked --no-install-project

COPY . /app

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked


# actual application image
FROM python:3.14-trixie

ENV PYTHONUNBUFFERED=1

# Install system dependencies required at runtime
RUN set -x \
    && apt-get update \
    && apt-get install --no-install-recommends -y libpq5 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Setup a non-root user
RUN groupadd --system --gid 999 nonroot \
    && useradd --system --gid 999 --uid 999 --create-home nonroot

# Copy the application from the builder
COPY --from=build --chown=nonroot:nonroot /app/.venv/ /app/.venv/

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Use the non-root user to run our application
USER nonroot

# Use `/app` as the working directory
WORKDIR /app

# Run red_githubbot by default
CMD ["python", "-Om", "red_githubbot"]
