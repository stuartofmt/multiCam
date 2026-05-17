"""Inference functions."""

from io import BytesIO
from typing import Mapping, Optional, Union

import numpy as np
from PIL import Image, ImageEnhance
from torchvision import transforms


def _clamp(value: float, lower: float = 0.0, upper: float = 500.0) -> float:
    return max(lower, min(upper, value))


def apply_image_tuning(image: Image.Image, tuning: Optional[Mapping[str, object]]) -> Image.Image:
    """Apply brightness/contrast/sharpness adjustments from camera config."""
    if not tuning:
        return image

    try:
        brightness_factor = _clamp(float(tuning.get("brightness", 100.0))) / 100.0
        contrast_factor = _clamp(float(tuning.get("contrast", 100.0))) / 100.0
        sharpness_factor = _clamp(float(tuning.get("sharpness", 100.0))) / 100.0
    except Exception:
        # Fail open if config values are malformed
        return image

    image = ImageEnhance.Brightness(image).enhance(brightness_factor)
    image = ImageEnhance.Contrast(image).enhance(contrast_factor)
    image = ImageEnhance.Sharpness(image).enhance(sharpness_factor)
    return image


def get_transform():
    """Image preprocessing transform."""
    return transforms.Compose([
        transforms.Resize(256),
        transforms.Grayscale(num_output_channels=3),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])


def preprocess_image(image: Union[bytes, Image.Image], tuning: Optional[Mapping[str, object]] = None) -> np.ndarray:
    """Preprocess image for inference."""
    transform = get_transform()
    if isinstance(image, bytes):
        image = Image.open(BytesIO(image)).convert('RGB')
    else:
        image = image.convert('RGB')
    image = apply_image_tuning(image, tuning)
    tensor = transform(image)
    return tensor.unsqueeze(0).numpy()


def predict(
    image: Union[bytes, Image.Image],
    model_info: dict,
    sensitivity: float = 1.0,
    tuning: Optional[Mapping[str, object]] = None,
) -> dict:
    """Run prediction on an image."""
    image_array = preprocess_image(image, tuning)
    # Get embedding
    outputs = model_info["session"].run(
        [model_info["output_name"]], 
        {model_info["input_name"]: image_array}
    )
    embedding = outputs[0].flatten()
    # Compute distances to prototypes
    prototypes = model_info["prototypes"]
    distances = np.linalg.norm(prototypes - embedding, axis=1)
    # Apply sensitivity adjustment
    adjusted_distances = distances.copy()
    defect_idx = model_info["defect_idx"]
    if defect_idx >= 0 and sensitivity != 1.0:
        adjusted_distances[defect_idx] *= (1.0 / sensitivity)
    # Get prediction
    predicted_idx = int(np.argmin(adjusted_distances))
    class_names = model_info["class_names"]
    min_dist = adjusted_distances[predicted_idx]
    confidence = 1.0 / (1.0 + min_dist)
    return {
        "class_name": class_names[predicted_idx],
        "class_idx": predicted_idx,
        "confidence": float(confidence),
        "distances": {name: float(d) for name, d in zip(class_names, distances)},
    }
