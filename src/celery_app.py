from celery import Celery
from kombu.utils.functional import retry_over_time
import os
import logging
from functools import wraps
import asyncio

# Настройки соединения
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672//")
HEARTBEAT_INTERVAL = 300  # 5 минут (меньше чем 30-минутный таймаут RabbitMQ)
RECONNECT_DELAY = 1200  # 20 минут между принудительными переподключениями

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]'
)
logger = logging.getLogger(__name__)

def reconnect_on_failure_async(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return await func(*args, **kwargs)
            except ConnectionError as e:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)  # Асинхронная задержка
                # Закрываем соединение Celery
                from celery import current_app
                current_app.connection.close()
        return await func(*args, **kwargs)
    return wrapper

celery = Celery(
    __name__,
    broker=RABBITMQ_URL,
    backend="rpc://",
    include=["tasks"]
)

# Основные настройки
celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Europe/Moscow",
    enable_utc=True,
    
    task_acks_late=True,  # ACK tasks after execution
    task_reject_on_worker_lost=True,  # Requeue tasks if worker goes down
    worker_prefetch_multiplier=1,  
    # Настройки соединения и переподключения
    broker_connection_retry_on_startup=True,
    broker_heartbeat=HEARTBEAT_INTERVAL,
    broker_connection_timeout=30,
    broker_connection_max_retries=100,
    broker_connection_retry=True,
    broker_pool_limit=10,  # Set a reasonable limit instead of None
    broker_transport_options={
        'max_retries': 3,
        'interval_start': 0,
        'interval_step': 0.2,
        'interval_max': 0.5,
        'visibility_timeout': 43200,  # 12 hours
    }
)

# Периодическая задача для принудительного переподключения
@celery.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    sender.add_periodic_task(
        RECONNECT_DELAY,
        force_reconnect.s(),
        name='force-reconnect-every-20-min'
    )

@celery.task(bind=True, max_retries=3)
def force_reconnect(self):
    """Принудительное переподключение к брокеру"""
    try:
        logger.info("Performing scheduled broker reconnection")
        # Безопасное закрытие соединений
        try:
            with celery.pool.acquire(block=True) as conn:
                conn.close()
        except Exception as pool_err:
            logger.warning(f"Error closing connection pool: {str(pool_err)}")
            
        try:
            celery.connection.close()
        except Exception as conn_err:
            logger.warning(f"Error closing connection: {str(conn_err)}")
            
        # Переподключение
        try:
            celery.connection.connect()
            logger.info("Successfully reconnected to broker")
        except Exception as reconnect_err:
            logger.error(f"Reconnection error: {str(reconnect_err)}")
            self.retry(countdown=60)
    except Exception as e:
        logger.error(f"Force reconnect task failed: {str(e)}")