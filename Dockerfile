FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt requirements.txt
COPY requirements-arb.txt requirements-arb.txt

# Install deps for SDK and panel
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r requirements-arb.txt

# Copy code
COPY . .

EXPOSE 8000

ENV ARB_DB_PATH=/data/arb.db
VOLUME ["/data"]

CMD ["uvicorn", "arb.panel.server:app", "--host", "0.0.0.0", "--port", "8000"]

