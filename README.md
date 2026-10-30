# MCSS — Multi-Agent Customer Support System

A single-tenant customer support application with an AI-powered chat widget, admin console, and knowledge base.

## Quick Start

```bash
# Clone and enter directory
cd mcss

# Copy environment template
cp .env.example .env

# Start development environment
./scripts/dev-up.sh

# Run smoke tests
./scripts/smoke-test.sh
```

### Services

| Service | URL | Description |
|---------|-----|-------------|
| API | http://localhost:8000 | FastAPI backend |
| Admin Web | http://localhost:5173 | React admin console |
| Widget Demo | http://localhost:5174 | Embeddable widget demo |
| PostgreSQL | localhost:55432 | Database (internal port 5432) |

### Health Checks

```bash
curl http://localhost:8000/healthz  # {"status":"ok"}
curl http://localhost:8000/readyz   # {"status":"ok"} (verifies DB)
```

## Architecture

```
Visitor Browser ── Widget (IIFE) ──┐
                                   ├── API (FastAPI) ── PostgreSQL + pgvector
Admin Browser ── React Console ────┘        │
                                            └── LLM + Embedding Providers
```

- **API**: FastAPI, Python 3.11, SQLAlchemy 2, Alembic, Poetry
- **Admin Web**: React 19, TypeScript, Vite, React Router, Zustand, Tailwind CSS 4
- **Widget**: React 18, TypeScript, Vite, Shadow DOM, IIFE bundle
- **Database**: PostgreSQL 16 with pgvector for semantic search

## Development

### Prerequisites

- Docker and Docker Compose
- Node.js 22+ (for local frontend development)
- Python 3.11+ (for local backend development)

### Commands

```bash
# Start all services
./scripts/dev-up.sh

# Stop services (preserves data)
./scripts/dev-down.sh

# Run smoke tests
./scripts/smoke-test.sh

# Seed admin user
docker compose exec api python scripts/seed-admin.py

# View logs
docker compose logs -f api

# Run backend tests
docker compose exec api pytest -q

# Run frontend type-check
docker compose exec admin-web npm run type-check
docker compose exec widget npm run type-check

# Build frontend
docker compose exec admin-web npm run build
docker compose exec widget npm run build
```

### Environment Variables

Copy `.env.example` to `.env` and configure:

| Variable | Description | Default |
|----------|-------------|---------|
| `MCSS_ENV` | Environment (development/test/production) | development |
| `MCSS_DATABASE_URL` | PostgreSQL connection string | postgresql+psycopg://mcss:change-me@postgres:5432/mcss |
| `MCSS_JWT_SECRET` | JWT signing key (32+ chars) | **required** |
| `MCSS_CORS_ORIGINS` | Comma-separated allowed origins | http://localhost:5173,http://localhost:5174 |
| `MCSS_WIDGET_ALLOWED_ORIGIN` | Widget embedding origin | http://localhost:5174 |
| `MCSS_LLM_BASE_URL` | LLM provider base URL (or "fake") | fake |
| `MCSS_LLM_API_KEY` | LLM API key | |
| `MCSS_LLM_CHAT_MODEL` | Chat model name | |
| `MCSS_EMBEDDING_MODEL` | Embedding model name | |
| `MCSS_EMBEDDING_DIMENSIONS` | Embedding dimensions | 1536 |
| `MCSS_RETRIEVAL_MIN_SCORE` | Min cosine similarity for retrieval | 0.72 |
| `MCSS_ADMIN_EMAIL` | Bootstrap admin email | admin@example.com |
| `MCSS_ADMIN_PASSWORD` | Bootstrap admin password | **required** |

**Production requirements:**
- `MCSS_JWT_SECRET` ≥ 32 random characters
- `MCSS_CORS_ORIGINS` explicit domains (no wildcards)
- `MCSS_LLM_BASE_URL` real provider (not "fake")
- `MCSS_WIDGET_ALLOWED_ORIGIN` production widget domain

## Testing

### Backend

```bash
# Unit and integration tests (fake provider)
docker compose exec api pytest -q

# With coverage
docker compose exec api pytest --cov=app --cov-report=term-missing
```

### Frontend

```bash
# Type checking
docker compose exec admin-web npm run type-check
docker compose exec widget npm run type-check

# Production build
docker compose exec admin-web npm run build
docker compose exec widget npm run build
```

### E2E (Playwright)

```bash
# Install Playwright
docker compose exec admin-web npx playwright install --with-deps

# Run tests
docker compose exec admin-web npm run test:e2e
```

## Deployment

### Production Compose

```bash
# Create production environment file
cp .env.example .env.production
# Edit with production values

# Deploy with Caddy reverse proxy
docker compose -f compose.yaml -f compose.prod.yaml up -d
```

See `docs/runbook.md` for complete deployment, monitoring, and incident response procedures.

## Documentation

- [Architecture](docs/architecture.md) — System diagram, service boundaries, data flow
- [API Reference](docs/api.md) — Complete endpoint documentation
- [Threat Model](docs/threat-model.md) — STRIDE analysis and mitigations
- [Runbook](docs/runbook.md) — Operations, deployment, incidents
- [Decisions](docs/decisions.md) — Implementation decision log

## Project Structure

```
mcss/
├── AGENTS.md              # Agent instructions
├── LICENSE                # Apache-2.0
├── README.md              # This file
├── .env.example           # Environment template
├── .gitignore
├── compose.yaml           # Docker Compose (dev)
├── docs/
│   ├── architecture.md
│   ├── api.md
│   ├── threat-model.md
│   ├── runbook.md
│   └── decisions.md
├── api/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── dependencies.py
│   │   ├── api/routes/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── providers/
│   │   ├── prompts/
│   │   └── security/
│   └── tests/
├── admin-web/
│   ├── Dockerfile
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── index.html
│   └── src/
├── widget/
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── demo/
│   └── src/
└── scripts/
    ├── dev-up.sh
    ├── dev-down.sh
    ├── seed-admin.py
    └── smoke-test.sh
```

## License

Apache-2.0 — see [LICENSE](LICENSE) for details.

## Security

For security issues, see [threat-model.md](docs/threat-model.md) and contact security@example.com.
