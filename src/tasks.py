# tasks.py
import logging
from pathlib import Path
import tempfile
import shutil
import subprocess
import platform
import trimesh

from celery_app import celery
from slicer_utils import (
    find_slicer,
    validate_model,
    get_printing_parameters,
    PRINTER_CONFIG,
    FILAMENT_CONFIGS,
    PRINTER_PROFILE
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@celery.task(bind=True, max_retries=3, acks_late=True, time_limit=3600)
def calculate_print_task(self, file_content: bytes, infill: int, material: str) -> dict:
    temp_dir = tempfile.mkdtemp()
    stl_path = Path(temp_dir) / "model.stl"
    gcode_path = Path(temp_dir) / "output.gcode"

    try:
        self.update_state(state='PROGRESS', meta={'progress': 5, 'calculation': 'Preparing model'})
        stl_path.write_bytes(file_content)

        self.update_state(state='PROGRESS', meta={'progress': 15, 'calculation': 'Loading model'})
        mesh = trimesh.load_mesh(str(stl_path))

        if not isinstance(mesh, trimesh.Trimesh):
            raise ValueError("Provided file is not a valid 3D mesh")

        self.update_state(state='PROGRESS', meta={'progress': 35, 'calculation': 'Validating model'})
        validation = validate_model(mesh)
        if not validation["is_valid"]:
            return {"status": "error", "errors": validation["errors"]}

        slicer_command = [
            find_slicer(),
            "--load", str(PRINTER_CONFIG),
            "--load", str(FILAMENT_CONFIGS[material]),
            "--layer-height", str(PRINTER_PROFILE["layer_height"]),
            "--fill-density", str(infill),
            "--export-gcode",
            "-o", str(gcode_path),
            str(stl_path)
        ]

        self.update_state(state='PROGRESS', meta={'progress': 60, 'calculation': 'Running slicer'})
        result = subprocess.run(
            slicer_command,
            capture_output=True,
            text=True,
            shell=platform.system() == "Windows",
            timeout=1800
        )

        if result.returncode != 0:
            raise RuntimeError(f"Slicer failed: {result.stderr.strip()}")

        self.update_state(state='PROGRESS', meta={'progress': 85, 'calculation': 'Parsing results'})
        print_params = get_printing_parameters(gcode_path)
        return {
            "status": "SUCCESS",
            "result": {
                "print_time": print_params.get("printing_time", "0h 0m"),
                "material_grams": print_params.get("total_filament_used_grams", 0.0),
                "cost": print_params.get("total_filament_cost", 0.0),
            }
        }

    except Exception as e:
        logger.exception("Task failed")
        self.update_state(state="FAILURE", meta={"error": str(e)})
        return {"status": "ERROR", "error": str(e)}

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
