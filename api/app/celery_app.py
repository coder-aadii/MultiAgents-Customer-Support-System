from celery import Celery

from app.config import settings

celery_app = Celery(
    "mcss",
    broker=settings.MCSS_CELERY_BROKER_URL,
    backend=settings.MCSS_CELERY_RESULT_BACKEND,
    include=["app.tasks.health"],
)
celery_app.conf.update(
    task_default_queue="mcss",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    beat_schedule={"mcss-health-pulse": {"task": "mcss.health.pulse", "schedule": 300.0}},
)
