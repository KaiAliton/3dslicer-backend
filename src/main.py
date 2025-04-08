# main.py
import os
import aiofiles
from dotenv import load_dotenv, dotenv_values
import datetime
from pathlib import Path
import platform
import logging
import shutil
from fastapi import Depends, FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from celery.result import AsyncResult
from typing import Dict
from sqlalchemy import Column, DateTime, String, Float, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from fastapi.security import OAuth2PasswordBearer

from slicer_utils import validate_infill, PRINTER_PROFILE, FILAMENT_CONFIGS
from tasks import calculate_print_task

dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

engine = create_engine(os.getenv("SQLALCHEMY_DATABASE_URL"))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Order(Base):
    __tablename__ = "orders"
    id = Column(String, primary_key=True, index=True)
    email = Column(String, index=True)
    telegram = Column(String)
    file_path = Column(String)
    cost = Column(Float)
    print_time = Column(String)
    material_grams = Column(Float)
    status = Column(String, default="Не подтвержден") 
    order_date = Column(DateTime, default=datetime.datetime.now)
    address = Column(String, default="")
    
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]"
)
logger = logging.getLogger(__name__)

# FastAPI setup
app = FastAPI(title="3D Print Calculator API")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:5174",
        "https://localhost:5174",
        "http://localhost:5173",
        "https://localhost:5173",
        "http://localhost:8080",
        "https://3dslicer.vercel.app",
        "https://kaialitest.ru"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    logger.info("API startup initiated")
    logger.info(f"Printer: {PRINTER_PROFILE['name']}")
    logger.info(f"Materials: {list(FILAMENT_CONFIGS.keys())}")


@app.get("/ping")
async def health_check() -> Dict[str, str]:
    return {"status": "ok", "message": "Service is operational"}


@app.post("/calculate")
async def create_calculation_task(
    model: UploadFile = File(...),
    infill: int = Form(20, ge=0, le=100),
    material: str = Form("PLA")
) -> Dict[str, str]:
    if not model.filename.lower().endswith('.stl'):
        raise HTTPException(status_code=400, detail="Only .stl files are allowed")
    if material not in FILAMENT_CONFIGS:
        raise HTTPException(status_code=400, detail=f"Invalid material: {material}")
    try:
        content = await model.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")
    if not content:
        raise HTTPException(status_code=400, detail="Empty file received")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File size exceeds 10MB limit")

    task = calculate_print_task.delay(
        file_content=content,
        infill=validate_infill(infill),
        material=material
    )
    return {"task_id": task.id, "status_url": f"/task/{task.id}"}


@app.get("/task/{task_id}")
async def get_task_status(task_id: str) -> Dict:
    try:
        task = AsyncResult(task_id)
        response = {
            "task_id": task_id,
            "status": task.status,
            "progress": task.info.get("progress"),
            "result": None,
            "error": None
        }

        if task.ready():
            if task.successful():
                result = task.result
                response["status"] = "SUCCESS"
                response['progress'] = 100
                response["result"] = result.get("result") if isinstance(result, dict) else result
            elif task.failed():
                response["status"] = "ERROR"
                response["error"] = str(task.result)

        return response

    except Exception as e:
        logger.exception("Error checking task status")
        raise HTTPException(status_code=500, detail="Failed to retrieve task status")


@app.get("/printer-specs")
async def get_printer_specs() -> Dict:
    return {
        "printer": PRINTER_PROFILE,
        "materials": list(FILAMENT_CONFIGS.keys())
    }
    
async def save_upload_file(upload_file: UploadFile, destination: Path) -> None:
    async with aiofiles.open(destination, 'wb') as out_file:
        content = await upload_file.read()
        await out_file.write(content)    

@app.post("/makeorder")
async def make_order(
    task_id: str = Form(...),
    email: str = Form(...),
    telegram: str = Form(...),
    model: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    
    task = AsyncResult(task_id)
    if not task.ready():
        raise HTTPException(status_code=400, detail="Task not done yet")
    if task.failed():
        raise HTTPException(status_code=400, detail="Task finished with error")

    result = task.result.get("result") if isinstance(task.result, dict) else task.result
    if not result:
        raise HTTPException(status_code=400, detail="Incorrect task result")
    
    cost = result.get("cost")
    print_time = result.get("print_time")
    material_grams = result.get("material_grams")
    if cost is None or print_time is None or material_grams is None:
        raise HTTPException(status_code=400, detail="No further information")
    
    # Save file
    orders_dir = Path("orders")
    orders_dir.mkdir(parents=True, exist_ok=True)
    file_path = orders_dir / f"{task_id}.stl"
    await save_upload_file(model, file_path)
    
    # Save order
    order = Order(
        id=task_id,
        email=email,
        telegram=telegram,
        file_path=str(file_path),
        cost=cost,
        print_time=print_time,
        material_grams=material_grams,
        status="Не подтвержден"
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    
    
    return {"status": "Order placed successfuly", "order_id": order.id}


if __name__ == "__main__":
    import uvicorn
    if platform.system() == "Windows":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        ssl_keyfile="C:/Users/Kai/Desktop/projects/3dslicer-backend/src/key.key",
        ssl_certfile="C:/Users/Kai/Desktop/projects/3dslicer-backend/src/cert.crt",
    )
