# tasks.py
import logging
from celery_app import celery
from pathlib import Path
import tempfile
import shutil
import subprocess
import platform
import trimesh
from slicer_utils import (
    find_slicer,
    validate_infill,
    validate_model,
    get_printing_parameters,
    PRINTER_CONFIG,
    FILAMENT_CONFIGS,
    PRINTER_PROFILE
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]'
)
logger = logging.getLogger(__name__)

@celery.task(bind=True, max_retries=3, acks_late=True)
def calculate_print_task(self, file_content, infill, material):
    self.update_state(state='PROGRESS', meta={'progress': 10})
    temp_dir = tempfile.mkdtemp()
    try:
        stl_path = Path(temp_dir) / "model.stl"
        stl_path.write_bytes(file_content)
        
        mesh = trimesh.load(stl_path)
        if not isinstance(mesh, trimesh.Trimesh):
            raise ValueError("Unsupported file format")
        
        validation = validate_model(mesh)
        if not validation["is_valid"]:
            return {"status": "error", "errors": validation["errors"]}
        
        gcode_path = Path(temp_dir) / "output.gcode"
        slicer_cmd = [
            find_slicer(),
            "--load", str(PRINTER_CONFIG),
            "--load", str(FILAMENT_CONFIGS[material]),
            "--layer-height", str(PRINTER_PROFILE["layer_height"]),
            "--fill-density", str(infill),
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
            raise RuntimeError(f"Slicer error: {result.stderr}")
        
        self.update_state(state='PROGRESS', meta={'progress': 90})
        print_params = get_printing_parameters(gcode_path)
        
        return {
            "status": "success",
            "result": {
                "print_time": print_params['printing_time'],
                "material_grams": print_params['total_filament_used_grams'],
                "cost": print_params['total_filament_cost'],
            }
        }
        
    except Exception as e:
        logger.error(f"Print calculation error: {str(e)}", exc_info=True)
        self.retry(exc=e, countdown=30)
        return {"status": "error", "error": str(e)}
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as cleanup_err:
            logger.warning(f"Error cleaning temp directory: {str(cleanup_err)}")