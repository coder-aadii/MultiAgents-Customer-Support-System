from app.celery_app import celery_app


@celery_app.task(name="mcss.health.pulse")
def health_pulse() -> dict[str, str]:
    return {"status": "ok"}
