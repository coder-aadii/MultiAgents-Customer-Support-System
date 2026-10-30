FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==1.8.5

COPY pyproject.toml ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --with dev

COPY alembic.ini .
COPY alembic/ alembic/
COPY app/ app/

EXPOSE 8000
