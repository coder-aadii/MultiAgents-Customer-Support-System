#!/usr/bin/env bash
set -euo pipefail

# MCSS Development Environment Shutdown Script
# Stops services without deleting volumes or data

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "Stopping MCSS development environment..."

# Stop services but keep volumes
docker compose down

echo "MCSS development environment stopped."
echo "Data volumes are preserved. Run './scripts/dev-up.sh' to restart."