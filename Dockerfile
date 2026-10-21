FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce
COPY --from=ghcr.io/astral-sh/uv:0.12.0@sha256:606e70c71c852d03f611b1e56a195d08648507018a7057fab82c4974c4eae105 /uv /uvx /bin/
RUN apt-get update && apt-get install --no-install-recommends -y git libgomp1 && rm -rf /var/lib/apt/lists/*
ARG INSTALL_ML=false
# Fiona has no wheel for every Linux/Python/architecture combination. The ML
# extra can build its pinned source distribution against the system GDAL API.
RUN if [ "$INSTALL_ML" = "true" ]; then \
      apt-get update \
      && apt-get install --no-install-recommends -y build-essential libgdal-dev \
      && rm -rf /var/lib/apt/lists/*; \
    fi
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PUERTOGT_DATA_DIR=/data PUERTOGT_CONTAINER=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY config ./config
# HTTP/2 fetches were reset by the Docker build network; this affects transport only.
RUN --mount=type=cache,target=/root/.cache/uv if [ "$INSTALL_ML" = "true" ]; then \
      GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.version GIT_CONFIG_VALUE_0=HTTP/1.1 \
      uv sync --frozen --no-editable --no-dev --extra ml; \
    else uv sync --frozen --no-editable --no-dev; fi
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
USER app
ENV PATH="/app/.venv/bin:$PATH"
VOLUME /data
EXPOSE 8000
CMD ["puertogt", "serve", "--host", "0.0.0.0", "--port", "8000"]
