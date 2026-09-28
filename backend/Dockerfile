FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libcairo2 \
        libcairo2-dev \
        libwebp7 \
        libwebp-dev \
        pkg-config \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove gcc libcairo2-dev libwebp-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app

RUN mkdir -p /var/lib/mobilemap/tiles

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
