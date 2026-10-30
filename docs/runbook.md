# MCSS Runbook

## Quick Reference

| Service | Port | Health Check |
|---------|------|--------------|
| API | 8000 | `GET /healthz`, `GET /readyz` |
| Admin Web | 5173 | `GET /` returns 200 |
| Widget | 5174 | `GET /` returns 200 |
| PostgreSQL | 55432 | `pg_isready -U mcss -d mcss` |
| Redis | 56379 | `redis-cli ping` |
| WuKongIM | 5200 | HTTP health endpoint inside its container |
| Celery worker / Beat | internal | `celery inspect ping` |

## Common Operations

### Start Development Environment

```bash
# First time setup
cp .env.example .env
# Edit .env with your settings

# Start all services
./scripts/dev-up.sh

# Verify
./scripts/smoke-test.sh
```

### Stop Development Environment

```bash
./scripts/dev-down.sh
```

### View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f api
docker compose logs -f admin-web
docker compose logs -f widget
docker compose logs -f postgres
```

### Database Operations

```bash
# Connect to database
docker compose exec postgres psql -U mcss -d mcss

# Run migrations manually
docker compose exec api alembic upgrade head

# Create migration
docker compose exec api alembic revision --autogenerate -m "description"

# Backup database
docker compose exec postgres pg_dump -U mcss mcss > backup.sql

# Restore database
cat backup.sql | docker compose exec -T postgres psql -U mcss -d mcss
```

### Seed Admin User

```bash
# Requires MCSS_ADMIN_EMAIL and MCSS_ADMIN_PASSWORD in .env
docker compose exec api python /workspace/scripts/seed-admin.py
```

### Reset Development Data (Destructive)

```bash
# WARNING: Deletes all data
docker compose down -v
./scripts/dev-up.sh
```

## Production Deployment

### Prerequisites

- Server with Docker and Docker Compose
- Domain with DNS configured
- TLS certificates (Caddy handles automatically)
- PostgreSQL 16 + pgvector (can use managed service)
- LLM provider credentials (OpenAI-compatible API)

### Configuration

Create `.env.production` with:

```bash
MCSS_ENV=production
MCSS_API_HOST=0.0.0.0
MCSS_API_PORT=8000
MCSS_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/mcss
MCSS_JWT_SECRET=<32+ random chars>
MCSS_JWT_ACCESS_MINUTES=15
MCSS_JWT_REFRESH_DAYS=7
MCSS_CORS_ORIGINS=https://support.example.com
MCSS_WIDGET_ALLOWED_ORIGIN=https://example.com
MCSS_LLM_BASE_URL=https://api.openai.com/v1
MCSS_LLM_API_KEY=sk-...
MCSS_LLM_CHAT_MODEL=gpt-4o-mini
MCSS_EMBEDDING_MODEL=text-embedding-3-small
MCSS_EMBEDDING_DIMENSIONS=1536
MCSS_RETRIEVAL_MIN_SCORE=0.72
MCSS_ADMIN_EMAIL=admin@example.com
MCSS_ADMIN_PASSWORD=<strong password>
```

### Deploy with Caddy

```bash
# Start services
docker compose -f compose.yaml -f compose.prod.yaml up -d

# Caddy will automatically provision TLS for your domain
```

### compose.prod.yaml

```yaml
services:
  caddy:
    image: caddy:2-alpine
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy-data:/data
      - caddy-config:/config
    depends_on:
      - api
      - admin-web

  api:
    environment:
      MCSS_ENV: production
    # Remove volumes and reload for production

  admin-web:
    # Build production image, serve static files

volumes:
  caddy-data:
  caddy-config:
```

### Caddyfile

```
support.example.com {
    reverse_proxy api:8000
}

admin.example.com {
    root * /app/dist
    file_server
    try_files {path} /index.html
}

example.com {
    root * /app/dist
    file_server
}
```

## Monitoring

### Health Checks

```bash
# API health
curl -fsS https://support.example.com/healthz

# API ready (includes DB)
curl -fsS https://support.example.com/readyz

# Database
docker compose exec postgres pg_isready -U mcss -d mcss
```

### Key Metrics to Watch

- API response latency (p50, p95, p99)
- Error rate by endpoint
- Database connection pool usage
- PostgreSQL disk usage
- LLM provider latency and error rate
- Handoff queue depth
- Active conversations count

### Log Analysis

```bash
# Recent errors
docker compose logs api --since=1h | grep -i error

# Authentication failures
docker compose logs api --since=1h | grep -i "unauthorized\|invalid"

# Handoff events
docker compose logs api --since=1h | grep -i handoff
```

## Incident Response

### API Unhealthy

1. Check `/healthz` and `/readyz`
2. If `/readyz` fails: check PostgreSQL connectivity
3. Check API logs: `docker compose logs api --tail=100`
4. Restart API: `docker compose restart api`

### Database Issues

1. Check PostgreSQL logs: `docker compose logs postgres`
2. Check disk space: `docker compose exec postgres df -h`
3. Check connections: `docker compose exec postgres psql -U mcss -c "SELECT count(*) FROM pg_stat_activity;"`
4. If corrupted: restore from backup

### High Handoff Rate

1. Check `/api/v1/handoffs?status=queued`
2. Review handoff reasons in admin console
3. Check knowledge base coverage
4. Adjust `MCSS_RETRIEVAL_MIN_SCORE` if too aggressive
5. Add missing documents to knowledge base

### LLM Provider Issues

1. Check circuit breaker status in logs
2. Verify provider API key and quota
3. If persistent: fallback to fake provider by setting `MCSS_LLM_BASE_URL=fake`
4. Review classification/answer prompts

### Widget Not Loading

1. Check browser console for errors
2. Verify `MCSS_WIDGET_ALLOWED_ORIGIN` matches embedding site
3. Check CORS headers on API responses
4. Verify widget bundle builds: `docker compose exec widget npm run build`

## Backup & Recovery

### Daily Backup (Cron)

```bash
#!/bin/bash
# /etc/cron.daily/mcss-backup
DATE=$(date +%Y%m%d)
docker compose exec -T postgres pg_dump -U mcss mcss | gzip > /backups/mcss-$DATE.sql.gz
# Keep 30 days
find /backups -name "mcss-*.sql.gz" -mtime +30 -delete
```

### Recovery Procedure

1. Stop API: `docker compose stop api`
2. Restore database from backup
3. Run migrations: `docker compose exec api alembic upgrade head`
4. Start API: `docker compose start api`
5. Verify with smoke test

## Security Operations

### Rotate JWT Secret

1. Generate new secret: `openssl rand -base64 48`
2. Update `.env` and restart API
3. All existing sessions will be invalidated

### Rotate LLM API Key

1. Generate new key in provider console
2. Update `.env` and restart API
3. Revoke old key

### Audit Log Review

```bash
# Recent admin actions
curl -H "Authorization: Bearer <admin_token>" \
  https://support.example.com/api/v1/audit-events?limit=100

# Failed login attempts
docker compose logs api | grep -i "login.*fail"
```

## Maintenance Windows

- Schedule during low-traffic periods
- Notify staff via admin console banner
- Run database maintenance (VACUUM, ANALYZE) monthly
- Update dependencies quarterly

## Contact & Escalation

- Primary: DevOps team
- Secondary: Backend team
- Security incidents: security@example.com
