import subprocess
import platform
import tempfile
import shutil
import logging
import os
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse
import trimesh
from trimesh.path import Path3D
import numpy as np

app = FastAPI()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]'
)
logger = logging.getLogger(__name__)

origins = [
    "http://localhost",
    "http://localhost:5173",
    "http://localhost:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent
CONFIG_DIR = BASE_DIR / "configs"
PRINTER_CONFIG = CONFIG_DIR / "config.ini"
FILAMENT_CONFIGS = {
    "PLA": CONFIG_DIR / "filament_PLA.ini",
    "ABS": CONFIG_DIR / "filament_ABS.ini"
}
PRINTER_PROFILE = {
    "name": "Prusa i3 MK3S+",
    "bed_size": [250, 210, 210],
    "nozzle_diameter": 0.4,
    "layer_height": 0.2,
    "print_speed": 50,
    "travel_speed": 120,
    "filament_diameter": 1.75,
}

def find_slicer() -> str:
    """Поиск PrusaSlicer в системе"""
    try:
        if "PRUSA_SLICER_PATH" in os.environ:
            custom_path = Path(os.environ["PRUSA_SLICER_PATH"])
            if custom_path.exists():
                return str(custom_path)

        slicer_name = "prusa-slicer.exe" if platform.system() == "Windows" else "prusa-slicer"
        default_paths = {
            "Windows": [
                r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer.exe",
                r"C:\Program Files (x86)\Prusa3D\PrusaSlicer\prusa-slicer.exe"
            ],
            "Linux": ["/usr/bin/prusa-slicer"],
            "Darwin": ["/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer"]
        }
        
        path_slicer = shutil.which(slicer_name)
        if path_slicer:
            return path_slicer
        
        for path in default_paths.get(platform.system(), []):
            if Path(path).exists():
                return path
        
        raise FileNotFoundError()
    
    except Exception as e:
        logger.exception("PrusaSlicer не найден")
        raise RuntimeError(
            "Установите PrusaSlicer и добавьте в PATH или задайте PRUSA_SLICER_PATH"
        )
    
def validate_infill(infill: int) -> float:
    if not 0 <= infill <= 100:
        raise ValueError("Заполнение должно быть между 0 и 100%")
    return infill / 100

def validate_model(mesh: trimesh.Trimesh) -> dict:
    errors = []
    if abs(mesh.volume) < 1e-3:
        errors.append("Нулевой объем")
    if not mesh.is_winding_consistent:
        errors.append("Ошибка нормалей")
    if any(mesh.bounding_box.extents > PRINTER_PROFILE["bed_size"]):
        errors.append("Превышены размеры стола")
    
    return {"is_valid": len(errors) == 0, "errors": errors}

@app.on_event("startup")
async def startup():
    try:
        find_slicer() 
    except Exception as e:
        logger.exception("Ошибка при запуске")
        raise e

@app.post("/calculate")
async def calculate_print(
    model: UploadFile = File(...),
    infill: int = Form(20, ge=0, le=100),
    material: str = Form("PLA"),
):
    temp_dir = tempfile.mkdtemp()
    try:
        fill_density = validate_infill(infill)
        stl_path = Path(temp_dir) / "model.stl"
        stl_path.write_bytes(await model.read())
        
        mesh = trimesh.load(stl_path)
        if not isinstance(mesh, trimesh.Trimesh):
            raise HTTPException(400, "Неподдерживаемый формат файла")
        
        validation = validate_model(mesh)
        if not validation["is_valid"]:
            return JSONResponse(
                status_code=400,
                content={"errors": validation["errors"]}
            )
        
        gcode_path = Path(temp_dir) / "output.gcode"
        slicer_cmd = [
            find_slicer(),
            "--load", str(PRINTER_CONFIG),
            "--load", str(FILAMENT_CONFIGS[material]),
            "--layer-height", str(PRINTER_PROFILE["layer_height"]),
            "--fill-density", str(fill_density),
            "--export-gcode",
            "-o", str(gcode_path),
            str(stl_path)
        ]
        
        result = subprocess.run(
            slicer_cmd,
            capture_output=True,
            text=True,
            shell=platform.system() == "Windows"
        )
        
        if result.returncode != 0:
            logger.error(f"Slicer error: {result.stderr}")
            raise HTTPException(500, "Ошибка слайсинга")
        
        print_params = get_printing_parameters(gcode_path)
        
        response = {
            "print_time": print_params['printing_time'],
            "material_grams": print_params['total_filament_used_grams'],
            "cost": print_params['total_filament_cost'],
        }
        
        return response
    
    except Exception as e:
        logger.exception("Ошибка в calculate_print") 
        raise HTTPException(500, "Internal server error")
    
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def get_printing_parameters(gcode_path: Path) -> float:
    params = {
        "printing_time": '',
        "total_filament_used_grams": 0,
        "total_filament_cost": 0,
    }
    try:
        with open(gcode_path, 'r') as f:
            for line in f:
                if line.startswith('; estimated printing time'):
                    params['printing_time'] = line.split('=')[1].strip()
                if line.startswith('; total filament used [g]'):
                    params['total_filament_used_grams'] = line.split('=')[1].strip()
                if line.startswith('; total filament cost'):
                    params['total_filament_cost'] = line.split('=')[1].strip()   
        return params
    except Exception as e:
        logger.warning("Ошибка парсинга времени", exc_info=True)
        return params

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)