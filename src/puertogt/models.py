"""Immutable, portable model registration and scoped processing identities."""

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from puertogt.contracts import ModelManifest
from puertogt.store import Store, confined_path, json_text, sha256_file


def processing_recipe(model: ModelManifest, attributes: ModelManifest | None = None) -> str:
    """Preprocessing alone cannot identify runs with different weights or thresholds."""
    identity = {
        key: model.model_dump(mode="json")[key]
        for key in (
            "model_id",
            "version",
            "recipe_version",
            "weights_sha256",
            "code_commit",
            "bands",
            "units",
            "publication_policy",
        )
    }
    if attributes:
        identity["attributes"] = processing_recipe(attributes)
    digest = hashlib.sha256(json_text(identity).encode()).hexdigest()[:16]
    return f"{model.recipe_version}:{digest}"


def validated_scope(model: ModelManifest, port_id: str, zone_id: str) -> bool:
    policy = model.publication_policy
    scope = policy.get("strata", {}).get(f"{port_id}|{model.sensor}|{zone_id}", {})
    return (
        policy.get("status") == "validated"
        and policy.get("automatic_publication") is True
        and scope.get("eligible") is True
    )


def register_portable_model(store: Store, model: ModelManifest) -> ModelManifest:
    """Copy verified weights atomically; keep embedded encoders in the same volume."""
    metadata = dict(model.metadata)
    if metadata.get("encoder_manifest"):
        encoder = ModelManifest.model_validate(metadata["encoder_manifest"])
        metadata["encoder_manifest"] = register_portable_model(store, encoder).model_dump(
            mode="json"
        )
        metadata.pop("encoder_manifest_path", None)
    relative = None
    if model.weights_path:
        source = Path(model.weights_path)
        if not source.is_absolute() and not source.is_file():
            source = confined_path(store.data_dir, source)
        if not model.weights_sha256 or sha256_file(source) != model.weights_sha256:
            raise ValueError("Model weights are missing or checksum mismatched")
        relative = f"models/{model.weights_sha256}.pt"
        target = confined_path(store.data_dir, relative, must_exist=False)
        target.parent.mkdir(exist_ok=True)
        if not target.exists():
            descriptor, name = tempfile.mkstemp(prefix=".model-", dir=target.parent)
            try:
                with os.fdopen(descriptor, "wb") as output, source.open("rb") as original:
                    shutil.copyfileobj(original, output)
                    output.flush()
                    os.fsync(output.fileno())
                if sha256_file(Path(name)) != model.weights_sha256:
                    raise ValueError("Model changed while copying")
                try:
                    os.link(name, target)
                except FileExistsError:
                    pass
            finally:
                Path(name).unlink(missing_ok=True)
        if sha256_file(target) != model.weights_sha256:
            raise ValueError("Stored model checksum mismatch")
    registered = model.model_copy(update={"weights_path": relative, "metadata": metadata})
    store.register_model(registered)
    return registered
