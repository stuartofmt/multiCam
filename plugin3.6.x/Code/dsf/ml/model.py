"""Model loading and state management."""

import json
import pickle

import onnxruntime as ort
from huggingface_hub import hf_hub_download

from ..config import settings

_model_info: dict | None = None


def download_model(force: bool = False) -> None:
    model_dir = settings.MODEL_DIR
    for filename in settings.MODEL_FILES:
        filepath = model_dir / filename
        if force or not filepath.exists():
            hf_hub_download(
                repo_id=settings.MODEL_REPO_ID,
                filename=filename,
                local_dir=model_dir
            )


def load_model() -> dict:
    """Load and return model info dict."""
    global _model_info
    if _model_info is not None:
        return _model_info
    model_dir = settings.MODEL_DIR
    session_options = ort.SessionOptions()
    session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(
        str(model_dir / "model.onnx"),
        sess_options=session_options,
        providers=['CPUExecutionProvider']
    )
    with open(model_dir / "opt.json") as f:
        options = json.load(f)
    with open(model_dir / "prototypes.pkl", "rb") as f:
        prototype_data = pickle.load(f)
    _model_info = {
        "session": session,
        "input_name": session.get_inputs()[0].name,
        "output_name": session.get_outputs()[0].name,
        "prototypes": prototype_data["prototypes"],
        "class_names": prototype_data["class_names"],
        "defect_idx": prototype_data["defect_idx"],
        "options": options,
    }
    return _model_info


def get_model() -> dict:
    """Get loaded model info."""
    if _model_info is None:
        raise RuntimeError("Model not loaded")
    return _model_info
