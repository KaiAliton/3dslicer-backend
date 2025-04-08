
import platform
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from celery.result import AsyncResult
from pathlib import Path
import logging
import asyncio
from typing import Dict
from slicer_utils import (
    validate_infill,
    PRINTER_PROFILE,
    FILAMENT_CONFIGS
)
from tasks import calculate_print_task

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="3D Print Calculator API")

# CORS Configuration
origins = [
    "http://localhost",
    "https://localhost:5174",
    "http://localhost:5174",
    "http://localhost:8080",
    "https://3dslicer.vercel.app",
    "kaialitest.ru"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """Проверка конфигурации при старте"""
    logger.info("Starting API with configuration:")
    logger.info(f"Printer: {PRINTER_PROFILE['name']}")
    logger.info(f"Available materials: {list(FILAMENT_CONFIGS.keys())}")

@app.get("/ping")
async def health_check() -> Dict[str, str]:
    """Проверка работоспособности сервиса"""
    return {"status": "ok", "message": "Service is operational"}

@app.post("/calculate")
async def create_calculation_task(
    model: UploadFile = File(..., description="STL model file"),
    infill: int = Form(20, ge=0, le=100, description="Infill percentage"),
    material: str = Form("PLA", description="Material type")
) -> Dict[str, str]:
    """
    Создание задачи для расчета параметров печати
    """
    try:
        # Валидация входных данных
        if material not in FILAMENT_CONFIGS:
            raise HTTPException(400, f"Invalid material: {material}")

        file_content = await model.read()
        if len(file_content) > 10 * 1024 * 1024:  # 10MB лимит
            raise HTTPException(413, "File size exceeds 10MB limit")

        # Создание задачи Celery
        task = calculate_print_task.delay(
            file_content=file_content,
            infill=validate_infill(infill),
            material=material
        )
        
        return {"task_id": task.id, "status_url": f"/task/{task.id}"}

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Calculation error: {str(e)}")
        raise HTTPException(500, "Internal server error")

@app.get("/task/{task_id}")
async def get_task_status(task_id: str) -> Dict:
    """
    Получение статуса задачи по ID с надежной обработкой ошибок
    """
    try:
        logger.info(f"Checking status for task: {task_id}")
        task = AsyncResult(task_id)
        
        # Создаем базовый ответ
        response = {
            "task_id": task_id,
            "status": "pending",
            "result": None,
            "error": None
        }
        
        try:
            # Безопасно получаем статус
            status = task.status
            response["status"] = status
            
            # Проверяем завершенность задачи
            if task.ready():
                try:
                    # Безопасно получаем результат
                    if task.successful():
                        result = task.result
                        response["result"] = result.get("result") if isinstance(result, dict) else result
                        response["status"] = "success"
                    elif task.failed():
                        response["status"] = "error"
                        response["error"] = str(task.result) if task.result else "Task failed"
                except Exception as result_err:
                    logger.warning(f"Error fetching result: {str(result_err)}")
                    response["status"] = "error"
                    response["error"] = "Error retrieving task result"
        except Exception as status_err:
            logger.warning(f"Error fetching status: {str(status_err)}")
            # Fallback to checking if task exists in database
            response["status"] = "unknown"
            
        return response
        
    except Exception as e:
        logger.error(f"Status check error: {str(e)}", exc_info=True)
        raise HTTPException(500, "Task status check failed")

@app.get("/printer-specs")
async def get_printer_specs() -> Dict:
    """Получение технических характеристик принтера"""
    return {
        "printer": PRINTER_PROFILE,
        "materials": list(FILAMENT_CONFIGS.keys())
    }
    

if __name__ == "__main__":
    import uvicorn
    import asyncio
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        ssl_keyfile="C:/Users/Kai/Desktop/projects/3dslicer-backend/src/key.key",
        ssl_certfile="C:/Users/Kai/Desktop/projects/3dslicer-backend/src/cert.crt",
        reload=True
    )