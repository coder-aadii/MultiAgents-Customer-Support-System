#!/usr/bin/env bash
set -euo pipefail

# MCSS Development Environment Startup Script
# Starts the full stack without resetting volumes, deleting data, or exposing secrets

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "Starting MCSS development environment..."

# Check for .env file
if [[ ! -f .env ]]; then
    echo "No .env file found. Copying from .env.example..."
    cp .env.example .env
    echo "Please edit .env with your configuration, then re-run this script."
    exit 1
fi

# Start services
echo "Starting Docker Compose services..."
docker compose up --build -d

# Wait for services to be healthy
echo "Waiting for services to be ready..."
sleep 5

# Check API health
echo "Checking API health..."
for i in {1..30}; do
    if curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then
        echo "API health check passed"
        break
    fi
    if [[ $i -eq 30 ]]; then
        echo "ERROR: API health check failed after 30 attempts"
        docker compose ps
        exit 1
    fi
    sleep 1
done

# Check API ready
echo "Checking API ready (database)..."
for i in {1..30}; do
    if curl -fsS http://localhost:8000/readyz >/dev/null 2>&1; then
        echo "API ready check passed"
        break
    fi
    if [[ $i -eq 30 ]]; then
        echo "ERROR: API ready check failed after 30 attempts"
        docker compose ps
        exit 1
    fi
    sleep 1
done

echo ""
echo "MCSS development environment is running!"
echo ""
echo "Services:"
echo "  API:        http://localhost:8000"
echo "  Admin Web:  http://localhost:5173"
echo "  Widget:     http://localhost:5174"
echo "  PostgreSQL: localhost:55432"
echo ""
echo "Note: Non-MVP services (Redis, WuKongIM, Celery) are deferred under the 'expanded' profile."
echo "      To launch with expanded services: docker compose --profile expanded up -d"
echo ""
echo "Run './scripts/smoke-test.sh' to verify the setup."
echo "Run './scripts/dev-down.sh' to stop the environment."
