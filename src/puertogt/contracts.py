"""Versioned contracts shared by ingestion, workers and the query service."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Sensor = Literal["sentinel-1", "sentinel-2"]
ObservationStatus = Literal["pending", "processing", "usable", "partial", "unusable", "failed"]
ReviewStatus = Literal["provisional", "reviewed", "rejected"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("a timezone is required; use UTC")
    return value.astimezone(timezone.utc)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Scene(Contract):
    scene_id: str
    acquisition_key: str
    sensor: Sensor
    product_level: str
    captured_at: datetime
    footprint: dict[str, Any]
    source_assets: dict[str, str] = Field(default_factory=dict)
    provider: str = "copernicus"
    published_at: datetime | None = None
    discovered_at: datetime = Field(default_factory=utcnow)
    port_ids: list[str] = Field(default_factory=list)
    orbit_direction: str | None = None
    relative_orbit: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("captured_at", "published_at", "discovered_at")
    @classmethod
    def utc_dates(cls, value):
        return as_utc(value) if value is not None else value


class Observation(Contract):
    observation_id: str
    acquisition_key: str
    zone_id: str
    recipe_version: str
    port_id: str
    sensor: Sensor
    captured_at: datetime
    status: ObservationStatus = "pending"
    observable_fraction: float | None = Field(default=None, ge=0, le=1)
    quality_flags: list[str] = Field(default_factory=list)
    processed_at: datetime | None = None
    model_id: str | None = None
    review_status: ReviewStatus = "provisional"
    detection_count: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("captured_at", "processed_at")
    @classmethod
    def utc_dates(cls, value):
        return as_utc(value) if value is not None else value

    @model_validator(mode="after")
    def observable_count(self):
        if self.status not in {"usable", "partial"} and self.detection_count is not None:
            raise ValueError("a non-observable observation must have a null count")
        return self


class RasterBundle(Contract):
    band_paths: dict[str, Path]
    mask_paths: dict[str, Path] = Field(default_factory=dict)
    crs: str
    transform: tuple[float, float, float, float, float, float]
    units: dict[str, str]
    preprocessing_version: str
    sensor: Sensor
    product_level: str
    observation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Prediction(Contract):
    prediction_id: str
    geometry: dict[str, Any]
    detection_score: float = Field(ge=0, le=1)
    family: Literal["carga", "tanquero", "pasajeros", "remolcador", "pesquero", "otros"] | None = (
        None
    )
    length_m: float | None = Field(default=None, gt=0)
    width_m: float | None = Field(default=None, gt=0)
    field_quality: dict[str, Any] = Field(default_factory=dict)
    evidence_asset_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChangeEvent(Contract):
    event_id: str
    geometry: dict[str, Any]
    reference_observation_id: str
    first_observation_id: str
    confirming_observation_id: str | None = None
    interval_start: datetime
    interval_end: datetime
    status: Literal["candidate", "persistent", "reverted", "rejected"] = "candidate"
    model_id: str
    evidence_asset_ids: list[str] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("interval_start", "interval_end")
    @classmethod
    def utc_dates(cls, value):
        return as_utc(value)


class ModelManifest(Contract):
    model_id: str
    version: str
    sensor: Sensor
    task: str
    recipe_version: str
    bands: list[str] = Field(default_factory=list)
    units: dict[str, str] = Field(default_factory=dict)
    weights_path: str | None = None
    weights_sha256: str | None = None
    code_commit: str | None = None
    dataset_version: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    publication_policy: dict[str, Any] = Field(default_factory=dict)
    licenses: list[dict[str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PortableBundle(Contract):
    format_version: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    files: dict[str, str]
    metadata: dict[str, Any] = Field(default_factory=dict)
