# BorderlessGuard demo: Streamlit UI on the simulated sandbox ledger. Offline, no API key needed.
#
#   docker build -t borderlessguard .
#   docker run --rm -p 8501:8501 borderlessguard                              # open http://localhost:8501
#   docker run --rm borderlessguard python scripts/verify_all.py              # full verification inside the image
#   docker run --rm -p 8501:8501 -e BG_PARSER=connected -e BG_MODEL_BASE_URL=https://... -e BG_MODEL_NAME=... 
#       -e BG_MODEL_API_KEY=... borderlessguard                                   # optional: a connected model
#
# The step-up one-time code in the demo is 246810. For anything beyond a demo, also pass -e BG_RECEIPT_KEY=<secret>.
FROM python:3.11-slim

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

COPY . .
RUN useradd --create-home --uid 10001 bg && chown -R bg:bg /app
USER bg

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "streamlit", "run", "ui/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
