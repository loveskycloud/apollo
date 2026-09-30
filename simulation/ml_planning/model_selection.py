"""Read the sole model selector; never consult environment variables or aliases."""
import hashlib
from pathlib import Path
import re


def read_model_selection(config_path, models_root):
    from google.protobuf import text_format
    from modules.simulation.ml_planning.proto.ml_planning_config_pb2 import MLPlanningConfig
    config_path=Path(config_path).resolve(strict=True)
    config=MLPlanningConfig()
    text_format.Parse(config_path.read_text(),config)
    if not config.IsInitialized() or not re.fullmatch(r"v[0-9]+(?:[._-][A-Za-z0-9]+)*",config.model_version):
        raise ValueError(f"Invalid model_version in {config_path}")
    weights=Path(models_root)/config.model_version/'unified.weights'
    weights=weights.resolve(strict=True)
    if not weights.is_file():
        raise ValueError(f"Model weights are not a file: {weights}")
    return {'version':config.model_version,'weights':str(weights),
            'sha256':hashlib.sha256(weights.read_bytes()).hexdigest()}
