FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY archantum/ archantum/

# Install Python dependencies
RUN pip install --no-cache-dir .

# Create data directory
RUN mkdir -p /data

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV DATABASE_PATH=/data/archantum.db

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -m archantum health || exit 1

# Default command
CMD ["python", "-m", "archantum", "run"]
