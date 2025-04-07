# celery_app.py
from celery import Celery
import os

# Используем RPC бэкенд для результатов
rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672//")
result_backend = "rpc://"  # или "redis://localhost:6379/0"

celery = Celery(
    __name__,
    broker=rabbitmq_url,
    backend=result_backend,
    include=["tasks"]
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Europe/Moscow",
    enable_utc=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1
)