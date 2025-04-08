# slicer_utils.py
import subprocess
import platform
import shutil
import logging
import os
from pathlib import Path
import trimesh

logger = logging.getLogger(__name__)

# Конфигурационные пути
BASE_DIR = Path(__file__).parent
CONFIG_DIR = BASE_DIR / "configs"
PRINTER_CONFIG = CONFIG_DIR / "config.ini"
FILAMENT_CONFIGS = {
    "PLA": CONFIG_DIR / "filament_PLA.ini",
    "ABS": CONFIG_DIR / "filament_ABS.ini"
}

# Параметры принтера
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
        
        raise FileNotFoundError("PrusaSlicer not found in default locations")
    
    except Exception as e:
        logger.exception("PrusaSlicer не найден")
        raise RuntimeError(
            "Install PrusaSlicer and add PATH or paste PRUSA_SLICER_PATH"
        ) from e

def validate_infill(infill: int) -> float:
    if not 0 <= infill <= 100:
        raise ValueError("Infill must be between 0 and 100%")
    return infill / 100

def validate_model(mesh: trimesh.Trimesh) -> dict:
    errors = []
    
    # Проверка объема
    if abs(mesh.volume) < 1e-3:
        errors.append("Null volume")
    
    # Проверка нормалей
    if not mesh.is_winding_consistent:
        errors.append("Error validating normals")
    
    # Проверка размеров
    bed_size = PRINTER_PROFILE["bed_size"]
    model_size = mesh.bounding_box.extents
    if any(s > b for s, b in zip(model_size, bed_size)):
        errors.append(
            f"Model volume is more than bed volume ({model_size} > {bed_size})"
        )
    
    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "model_size": model_size.tolist(),
        "volume": mesh.volume
    }

def get_printing_parameters(gcode_path: Path) -> dict:
    params = {
        "printing_time": "0h 0m",
        "total_filament_used_grams": 0.0,
        "total_filament_cost": 0.0,
    }
    
    try:
        with open(gcode_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('; estimated printing time'):
                    params['printing_time'] = line.split('=')[1].strip()
                elif line.startswith('; total filament used [g]'):
                    params['total_filament_used_grams'] = float(
                        line.split('=')[1].strip()
                    )
                elif line.startswith('; total filament cost'):
                    params['total_filament_cost'] = float(
                        line.split('=')[1].strip().replace('$', '')
                    )
        return params
    except Exception as e:
        logger.error(f"Ошибка парсинга G-кода: {str(e)}")
        return params