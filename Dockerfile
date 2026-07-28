# Network Incident Agent — container image.
# Runs the ADK Investigation agent (adk web/api) by default. The same image
# runs the triage/remediation agents and the embedding service via overridden
# commands. Portable: identical on GKE and on GDC bare metal.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SENTENCE_TRANSFORMERS_HOME=/models \
    HF_HOME=/models

WORKDIR /app

# System deps for sentence-transformers / torch wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Pre-download the embedding model into the image so bare-metal/air-gapped
# pods need no internet at runtime.
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('all-MiniLM-L6-v2')"

COPY . .

EXPOSE 8000
# adk api_server exposes the agent over HTTP for a Service/LoadBalancer.
CMD ["adk", "api_server", "network_incident_agent", "--host", "0.0.0.0", "--port", "8000"]
