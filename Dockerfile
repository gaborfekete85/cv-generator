# syntax=docker/dockerfile:1.6

# ============================================================================
# Stage 1: builder
# ----------------------------------------------------------------------------
# Some of our transitive dependencies don't ship prebuilt wheels for every
# Python / architecture combo (notably `pycairo`, pulled in by
# xhtml2pdf -> svglib -> rlPyCairo). When pip can't find a wheel, it compiles
# from source — which needs a C toolchain and the Cairo dev headers.
#
# We do that work in this throwaway "builder" stage. The resulting wheels
# are copied into the slim runtime stage below, so we don't ship gcc + 300MB
# of dev headers in the final image.
# ============================================================================
FROM python:3.12-slim-bookworm AS builder

# Only the dev headers needed to compile pycairo from source when no wheel
# is available (pycairo is pulled in transitively by xhtml2pdf → svglib →
# rlPyCairo). We previously also installed libpango1.0-dev / libharfbuzz-dev /
# libffi-dev for WeasyPrint — those are no longer required since we ship
# the slim xhtml2pdf-only stack.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    python3-dev \
    libcairo2-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

# Build every requirement into a wheel in /wheels. Anything that already has
# a wheel on PyPI is downloaded as-is; anything that needs a C build gets
# compiled here and its wheel cached.
RUN pip install --upgrade pip wheel \
    && pip wheel --no-cache-dir --wheel-dir /wheels \
        -r requirements.txt \
        --index-url https://pypi.org/simple


# ============================================================================
# Stage 2: runtime
# ----------------------------------------------------------------------------
# Only runtime libraries here — no compilers, no dev headers.
#
#   libpango-1.0-0, libpangoft2-1.0-0 — WeasyPrint text rendering
#   libharfbuzz0b, libffi8             — WeasyPrint transitive
#   libcairo2                          — used by pycairo / rlPyCairo at runtime
#   shared-mime-info                   — WeasyPrint file-type detection
#   fonts-dejavu-core                  — default sans/serif/mono fonts
# ============================================================================
FROM python:3.12-slim-bookworm

# Runtime deps:
#   libcairo2       — used by pycairo / rlPyCairo at runtime (xhtml2pdf
#                     needs this indirectly to rasterise embedded vectors)
#   fonts-dejavu-core — default sans / serif / mono so Unicode-ish text
#                       in CVs has something to fall back to
#
# Everything else (libpango*, libharfbuzz*, libffi8, shared-mime-info) was
# there for WeasyPrint. Since we no longer ship WeasyPrint, they're gone —
# saves ~80 MB on the final image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    libcairo2 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pre-built wheels produced by the builder stage. --no-index means
# pip will NOT reach out to PyPI — anything not in /wheels would fail here,
# which is exactly what we want (tells us the builder stage missed something
# rather than silently downloading a different version).
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links /wheels \
        -r requirements.txt \
    && rm -rf /wheels requirements.txt

# --- App code ---
COPY backend/  ./backend/
COPY frontend/ ./frontend/
COPY data/     ./data/

# Generated PDFs land here. docker-compose mounts this as a volume so the
# files survive container restarts.
RUN mkdir -p /app/output

# Unbuffered stdout/stderr — logs show up immediately under `docker logs`.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Liveness check — uses Python (already in the image) so we don't have to
# install curl/wget. Hits /health on loopback; any non-2xx = unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; \
r = urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3); \
sys.exit(0 if r.status == 200 else 1)" || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
