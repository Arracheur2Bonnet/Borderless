# BorderlessGuard demo: Streamlit UI on the simulated sandbox ledger. Offline, no API key needed.
#
#   docker build -t borderlessguard .
#   docker run --rm -p 127.0.0.1:8501:8501 borderlessguard                    # open http://localhost:8501
#   docker run --rm borderlessguard python scripts/verify_all.py              # full verification inside the image
#   docker run --rm -p 127.0.0.1:8501:8501 -e BG_MODEL_BASE_URL=https://host/v1 -e BG_MODEL_NAME=<model> \
#       -e BG_MODEL_API_KEY=<key> borderlessguard      # optional: the sidebar form 'Connect a model' starts with this endpoint;
#                                                        press Connect (the key is used only for that endpoint)
#
# The step-up one-time code in the demo is 246810. For anything beyond a demo, also pass -e BG_RECEIPT_KEY=<secret>.
# Pinned by digest (multi-arch index of python:3.11.17-slim, 2026-10-09); Dependabot proposes updates.
FROM python:3.15.0-slim@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true

WORKDIR /app

# Dependencies first so the layer is cached across source changes.
COPY requirements-lock.txt .
RUN pip install -r requirements-lock.txt

# The code stays owned by root: the runtime user can read it but not rewrite it (the ledger is in memory).
# Only the files that scripts/run_scenarios.py regenerates (verify_all) are writable by that user.
COPY . .
RUN useradd --create-home --uid 10001 bg     && chown -R bg:bg /app/deliverables/evidence /app/deliverables/task_completion.md
USER bg

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).status == 200 else 1)"

# Inside the container the app must listen on all interfaces; publish it on the host with -p 127.0.0.1:8501:8501.
# allowedHosts (.streamlit/config.toml) still limits the Host header to localhost / 127.0.0.1.
CMD ["python", "-m", "streamlit", "run", "ui/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
