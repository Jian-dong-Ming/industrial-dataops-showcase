import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import EmailStr, field_validator, model_validator
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


class UserRole(StrEnum):
    ADMIN = "admin"
    ENGINEER = "engineer"
    OBSERVER = "observer"


class AssetStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class TagDataType(StrEnum):
    FLOAT = "float"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    STRING = "string"


class AcquisitionDesiredState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"


class AcquisitionConnectionState(StrEnum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class ImportFileFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"


class ImportLayout(StrEnum):
    LONG = "long"
    WIDE = "wide"


class ImportBatchStatus(StrEnum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    DUPLICATE = "duplicate"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class DataQualityIssueType(StrEnum):
    MISSING_REQUIRED = "missing_required"
    INVALID_TIMESTAMP = "invalid_timestamp"
    INVALID_VALUE = "invalid_value"
    OUT_OF_RANGE = "out_of_range"
    UNKNOWN_TAG = "unknown_tag"
    AMBIGUOUS_TAG = "ambiguous_tag"
    DUPLICATE_IN_FILE = "duplicate_in_file"
    DUPLICATE_EXISTING = "duplicate_existing"
    BAD_QUALITY = "bad_quality"


class DataQualitySeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class SampleSourceType(StrEnum):
    OPCUA = "opcua"
    FILE = "file"


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    role: UserRole = Field(
        default=UserRole.OBSERVER,
        sa_type=Enum(  # type: ignore
            UserRole,
            name="user_role",
            native_enum=False,
            length=20,
            values_callable=lambda values: [item.value for item in values],
        ),
    )
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_superuser: bool | None = None
    role: UserRole | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    plant_accesses: list[UserPlantAccess] = Relationship(
        back_populates="user", cascade_delete=True
    )


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


class DashboardSummaryPublic(SQLModel):
    plant_count: int
    production_line_count: int
    device_count: int
    tag_count: int
    enabled_tag_count: int
    acquisition_task_count: int
    running_task_count: int
    connected_task_count: int
    sample_count: int
    latest_sample_at: datetime | None
    import_batch_count: int
    completed_import_batch_count: int
    quality_issue_count: int
    generated_at: datetime


# Shared properties
class AssetBase(SQLModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    status: AssetStatus = Field(
        default=AssetStatus.ACTIVE,
        sa_type=Enum(  # type: ignore
            AssetStatus,
            name="asset_status",
            native_enum=False,
            values_callable=lambda values: [item.value for item in values],
        ),
    )

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", value):
            raise ValueError(
                "Code must contain only uppercase letters, numbers, underscores, or hyphens"
            )
        return value


class PlantBase(AssetBase):
    location: str | None = Field(default=None, max_length=255)


class PlantCreate(PlantBase):
    pass


class PlantUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    location: str | None = Field(default=None, max_length=255)
    status: AssetStatus | None = None


class Plant(PlantBase, table=True):
    __tablename__ = "plant"
    __table_args__ = (
        UniqueConstraint("code", name="uq_plant_code"),
        Index("ix_plant_status", "status"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    production_lines: list[ProductionLine] = Relationship(back_populates="plant")
    user_accesses: list[UserPlantAccess] = Relationship(
        back_populates="plant", cascade_delete=True
    )


class PlantPublic(PlantBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class PlantsPublic(SQLModel):
    data: list[PlantPublic]
    count: int


class ProductionLineBase(AssetBase):
    process_type: str = Field(min_length=1, max_length=100)


class ProductionLineCreate(ProductionLineBase):
    plant_id: uuid.UUID


class ProductionLineUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    process_type: str | None = Field(default=None, min_length=1, max_length=100)
    status: AssetStatus | None = None


class ProductionLine(ProductionLineBase, table=True):
    __tablename__ = "production_line"
    __table_args__ = (
        UniqueConstraint("plant_id", "code", name="uq_production_line_plant_code"),
        Index("ix_production_line_plant_status", "plant_id", "status"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plant_id: uuid.UUID = Field(
        foreign_key="plant.id", nullable=False, ondelete="RESTRICT"
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    plant: Plant | None = Relationship(back_populates="production_lines")
    devices: list[Device] = Relationship(back_populates="production_line")


class ProductionLinePublic(ProductionLineBase):
    id: uuid.UUID
    plant_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ProductionLinesPublic(SQLModel):
    data: list[ProductionLinePublic]
    count: int


class DeviceBase(AssetBase):
    device_type: str = Field(min_length=1, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)


class DeviceCreate(DeviceBase):
    production_line_id: uuid.UUID


class DeviceUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    device_type: str | None = Field(default=None, min_length=1, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    status: AssetStatus | None = None


class Device(DeviceBase, table=True):
    __tablename__ = "device"
    __table_args__ = (
        UniqueConstraint("production_line_id", "code", name="uq_device_line_code"),
        Index("ix_device_line_status", "production_line_id", "status"),
        Index("ix_device_type", "device_type"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    production_line_id: uuid.UUID = Field(
        foreign_key="production_line.id", nullable=False, ondelete="RESTRICT"
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    production_line: ProductionLine | None = Relationship(back_populates="devices")
    tags: list[Tag] = Relationship(back_populates="device")


class DevicePublic(DeviceBase):
    id: uuid.UUID
    production_line_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class DevicesPublic(SQLModel):
    data: list[DevicePublic]
    count: int


class TagBase(SQLModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    data_type: TagDataType = Field(
        default=TagDataType.FLOAT,
        sa_type=Enum(  # type: ignore
            TagDataType,
            name="tag_data_type",
            native_enum=False,
            values_callable=lambda values: [item.value for item in values],
        ),
    )
    unit: str | None = Field(default=None, max_length=30)
    min_value: float | None = None
    max_value: float | None = None
    sampling_interval_ms: int = Field(default=1000, ge=100, le=86_400_000)
    is_enabled: bool = True

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", value):
            raise ValueError(
                "Code must contain only uppercase letters, numbers, underscores, or hyphens"
            )
        return value


class TagCreate(TagBase):
    device_id: uuid.UUID


class TagUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    data_type: TagDataType | None = None
    unit: str | None = Field(default=None, max_length=30)
    min_value: float | None = None
    max_value: float | None = None
    sampling_interval_ms: int | None = Field(default=None, ge=100, le=86_400_000)
    is_enabled: bool | None = None


class Tag(TagBase, table=True):
    __tablename__ = "tag"
    __table_args__ = (
        UniqueConstraint("device_id", "code", name="uq_tag_device_code"),
        CheckConstraint(
            "min_value IS NULL OR max_value IS NULL OR min_value <= max_value",
            name="ck_tag_value_range",
        ),
        Index("ix_tag_device_enabled", "device_id", "is_enabled"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    device_id: uuid.UUID = Field(
        foreign_key="device.id", nullable=False, ondelete="RESTRICT"
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    device: Device | None = Relationship(back_populates="tags")


class TagPublic(TagBase):
    id: uuid.UUID
    device_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class TagsPublic(SQLModel):
    data: list[TagPublic]
    count: int


class AcquisitionNodeInput(SQLModel):
    tag_id: uuid.UUID
    node_id: str = Field(min_length=1, max_length=512)
    is_enabled: bool = True


class AcquisitionTaskBase(SQLModel):
    name: str = Field(min_length=1, max_length=100)
    endpoint_url: str = Field(min_length=1, max_length=512)
    publishing_interval_ms: int = Field(default=1000, ge=100, le=60_000)
    batch_size: int = Field(default=100, ge=1, le=2000)
    reconnect_delay_seconds: int = Field(default=1, ge=1, le=300)
    max_reconnect_delay_seconds: int = Field(default=30, ge=1, le=900)

    @field_validator("endpoint_url")
    @classmethod
    def validate_endpoint_url(cls, value: str) -> str:
        endpoint = value.strip()
        parsed = urlparse(endpoint)
        if parsed.scheme != "opc.tcp" or not parsed.hostname or parsed.port is None:
            raise ValueError(
                "Endpoint URL must use opc.tcp:// and include a host and port"
            )
        if parsed.username or parsed.password:
            raise ValueError("Credentials must not be embedded in the endpoint URL")
        return endpoint


class AcquisitionTaskCreate(AcquisitionTaskBase):
    plant_id: uuid.UUID
    nodes: list[AcquisitionNodeInput] = Field(min_length=1, max_length=200)


class AcquisitionTaskUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    endpoint_url: str | None = Field(default=None, min_length=1, max_length=512)
    publishing_interval_ms: int | None = Field(default=None, ge=100, le=60_000)
    batch_size: int | None = Field(default=None, ge=1, le=2000)
    reconnect_delay_seconds: int | None = Field(default=None, ge=1, le=300)
    max_reconnect_delay_seconds: int | None = Field(default=None, ge=1, le=900)
    nodes: list[AcquisitionNodeInput] | None = Field(
        default=None, min_length=1, max_length=200
    )

    @field_validator("endpoint_url")
    @classmethod
    def validate_endpoint_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return AcquisitionTaskBase.validate_endpoint_url(value)


class AcquisitionTask(AcquisitionTaskBase, table=True):
    __tablename__ = "acquisition_task"
    __table_args__ = (
        UniqueConstraint("plant_id", "name", name="uq_acquisition_task_plant_name"),
        Index(
            "ix_acquisition_task_plant_desired",
            "plant_id",
            "desired_state",
        ),
        Index("ix_acquisition_task_heartbeat", "worker_heartbeat_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plant_id: uuid.UUID = Field(
        foreign_key="plant.id", nullable=False, ondelete="RESTRICT"
    )
    desired_state: AcquisitionDesiredState = Field(
        default=AcquisitionDesiredState.STOPPED,
        sa_type=Enum(  # type: ignore
            AcquisitionDesiredState,
            name="acquisition_desired_state",
            native_enum=False,
            values_callable=lambda values: [item.value for item in values],
        ),
    )
    connection_state: AcquisitionConnectionState = Field(
        default=AcquisitionConnectionState.STOPPED,
        sa_type=Enum(  # type: ignore
            AcquisitionConnectionState,
            name="acquisition_connection_state",
            native_enum=False,
            values_callable=lambda values: [item.value for item in values],
        ),
    )
    last_connected_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    last_disconnected_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    last_sample_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    worker_heartbeat_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    error_count: int = Field(default=0, ge=0)
    reconnect_count: int = Field(default=0, ge=0)
    samples_received: int = Field(default=0, ge=0)
    samples_written: int = Field(default=0, ge=0)
    duplicate_count: int = Field(default=0, ge=0)
    dropped_count: int = Field(default=0, ge=0)
    last_error: str | None = Field(default=None, max_length=2000)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class AcquisitionNode(SQLModel, table=True):
    __tablename__ = "acquisition_node"
    __table_args__ = (
        UniqueConstraint("task_id", "tag_id", name="uq_acquisition_node_task_tag"),
        UniqueConstraint("task_id", "node_id", name="uq_acquisition_node_task_node"),
        UniqueConstraint("tag_id", name="uq_acquisition_node_tag"),
        Index("ix_acquisition_node_task_enabled", "task_id", "is_enabled"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    task_id: uuid.UUID = Field(
        foreign_key="acquisition_task.id", nullable=False, ondelete="CASCADE"
    )
    tag_id: uuid.UUID = Field(foreign_key="tag.id", nullable=False, ondelete="RESTRICT")
    node_id: str = Field(min_length=1, max_length=512)
    is_enabled: bool = True
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class AcquisitionNodePublic(AcquisitionNodeInput):
    id: uuid.UUID
    task_id: uuid.UUID
    tag_code: str
    tag_name: str
    unit: str | None = None


class AcquisitionTaskPublic(AcquisitionTaskBase):
    id: uuid.UUID
    plant_id: uuid.UUID
    desired_state: AcquisitionDesiredState
    connection_state: AcquisitionConnectionState
    last_connected_at: datetime | None
    last_disconnected_at: datetime | None
    last_sample_at: datetime | None
    worker_heartbeat_at: datetime | None
    error_count: int
    reconnect_count: int
    samples_received: int
    samples_written: int
    duplicate_count: int
    dropped_count: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    nodes: list[AcquisitionNodePublic] = Field(default_factory=list)


class AcquisitionTasksPublic(SQLModel):
    data: list[AcquisitionTaskPublic]
    count: int


class OpcUaBrowseRequest(SQLModel):
    plant_id: uuid.UUID
    endpoint_url: str = Field(min_length=1, max_length=512)
    root_node_id: str = Field(default="i=85", min_length=1, max_length=512)
    max_depth: int = Field(default=5, ge=1, le=10)
    max_nodes: int = Field(default=500, ge=1, le=2000)

    @field_validator("endpoint_url")
    @classmethod
    def validate_endpoint_url(cls, value: str) -> str:
        return AcquisitionTaskBase.validate_endpoint_url(value)


class OpcUaBrowseNodePublic(SQLModel):
    node_id: str
    browse_name: str
    display_name: str
    data_type: str | None = None


class OpcUaBrowseResult(SQLModel):
    endpoint_url: str
    nodes: list[OpcUaBrowseNodePublic]
    truncated: bool


class ImportBatch(SQLModel, table=True):
    __tablename__ = "import_batch"
    __table_args__ = (
        Index("ix_import_batch_plant_created", "plant_id", "created_at"),
        Index("ix_import_batch_status_created", "status", "created_at"),
        Index("ix_import_batch_plant_sha256", "plant_id", "file_sha256"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plant_id: uuid.UUID = Field(
        foreign_key="plant.id", nullable=False, ondelete="RESTRICT"
    )
    created_by_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="RESTRICT"
    )
    duplicate_of_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="import_batch.id",
        ondelete="SET NULL",
    )
    original_filename: str = Field(max_length=255)
    storage_key: str | None = Field(default=None, max_length=512)
    file_format: ImportFileFormat = Field(
        sa_type=Enum(  # type: ignore
            ImportFileFormat,
            name="import_file_format",
            native_enum=False,
            values_callable=lambda values: [item.value for item in values],
        )
    )
    content_type: str | None = Field(default=None, max_length=255)
    file_size_bytes: int = Field(ge=0, sa_type=BigInteger)
    file_sha256: str = Field(min_length=64, max_length=64)
    file_encoding: str | None = Field(default=None, max_length=30)
    sheet_name: str | None = Field(default=None, max_length=255)
    mapping_config: dict[str, Any] | None = Field(default=None, sa_type=JSON)
    status: ImportBatchStatus = Field(
        default=ImportBatchStatus.UPLOADED,
        sa_type=Enum(  # type: ignore
            ImportBatchStatus,
            name="import_batch_status",
            native_enum=False,
            values_callable=lambda values: [item.value for item in values],
        ),
    )
    total_rows: int = Field(default=0, ge=0)
    accepted_rows: int = Field(default=0, ge=0)
    rejected_rows: int = Field(default=0, ge=0)
    duplicate_rows: int = Field(default=0, ge=0)
    warning_rows: int = Field(default=0, ge=0)
    issue_count: int = Field(default=0, ge=0)
    stored_issue_count: int = Field(default=0, ge=0)
    issues_truncated: bool = False
    error_message: str | None = Field(default=None, max_length=2000)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    started_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class ImportJob(SQLModel, table=True):
    __tablename__ = "import_job"
    __table_args__ = (
        Index("ix_import_job_status_heartbeat", "status", "heartbeat_at"),
        Index("ix_import_job_batch_created", "batch_id", "created_at"),
        Index(
            "uq_import_job_active_batch",
            "batch_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_import_job_status",
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    batch_id: uuid.UUID = Field(foreign_key="import_batch.id", ondelete="RESTRICT")
    requested_by_id: uuid.UUID = Field(foreign_key="user.id", ondelete="RESTRICT")
    mapping_config: dict[str, Any] = Field(sa_type=JSON)
    status: str = Field(default="queued", max_length=16)
    attempts: int = Field(default=0, ge=0)
    processed_rows: int = Field(default=0, ge=0)
    attempt_history: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)
    error_message: str | None = Field(default=None, max_length=2000)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    heartbeat_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))  # type: ignore
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))  # type: ignore


class ImportJobPublic(SQLModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    status: str
    attempts: int
    processed_rows: int
    attempt_history: list[dict[str, Any]]
    error_message: str | None
    created_at: datetime
    heartbeat_at: datetime | None
    completed_at: datetime | None


class ImportBatchPublic(SQLModel):
    id: uuid.UUID
    plant_id: uuid.UUID
    created_by_id: uuid.UUID
    duplicate_of_id: uuid.UUID | None
    original_filename: str
    source_file_available: bool
    file_format: ImportFileFormat
    content_type: str | None
    file_size_bytes: int
    file_sha256: str
    file_encoding: str | None
    sheet_name: str | None
    mapping_config: dict[str, Any] | None
    status: ImportBatchStatus
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int
    warning_rows: int
    issue_count: int
    stored_issue_count: int
    issues_truncated: bool
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ImportBatchesPublic(SQLModel):
    data: list[ImportBatchPublic]
    count: int


class WideColumnMapping(SQLModel):
    source_column: str = Field(min_length=1, max_length=255)
    tag_code: str = Field(min_length=1, max_length=100)
    device_code: str | None = Field(default=None, max_length=100)


class ImportMappingInput(SQLModel):
    layout: ImportLayout = ImportLayout.LONG
    timestamp_column: str = Field(min_length=1, max_length=255)
    tag_code_column: str | None = Field(default=None, max_length=255)
    value_column: str | None = Field(default=None, max_length=255)
    device_code_column: str | None = Field(default=None, max_length=255)
    quality_column: str | None = Field(default=None, max_length=255)
    wide_columns: list[WideColumnMapping] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_layout_mapping(self) -> ImportMappingInput:
        if self.layout == ImportLayout.LONG:
            if not self.tag_code_column or not self.value_column:
                raise ValueError("窄表必须映射测点编码列和测量值列")
            if self.wide_columns:
                raise ValueError("窄表不能同时提交宽表测量列映射")
            return self
        if self.tag_code_column or self.value_column or self.device_code_column:
            raise ValueError("宽表不使用测点编码列、测量值列或设备编码列")
        if not self.wide_columns:
            raise ValueError("宽表至少需要映射一个测量列")
        source_columns = [item.source_column for item in self.wide_columns]
        if len(source_columns) != len(set(source_columns)):
            raise ValueError("宽表中的同一源列只能映射一次")
        return self


class ImportTagOptionPublic(SQLModel):
    tag_id: uuid.UUID
    tag_code: str
    tag_name: str
    device_id: uuid.UUID
    device_code: str
    device_name: str


class ImportPreviewPublic(SQLModel):
    batch: ImportBatchPublic
    source_columns: list[str]
    preview_rows: list[dict[str, Any]]
    detected_layout: ImportLayout
    suggested_timestamp_column: str | None
    suggested_mapping: ImportMappingInput | None


class ImportFieldMapping(SQLModel, table=True):
    __tablename__ = "import_field_mapping"
    __table_args__ = (
        UniqueConstraint(
            "batch_id", "target_field", name="uq_import_mapping_batch_target"
        ),
        Index("ix_import_mapping_batch", "batch_id"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    batch_id: uuid.UUID = Field(
        foreign_key="import_batch.id", nullable=False, ondelete="CASCADE"
    )
    source_column: str = Field(max_length=255)
    target_field: str = Field(max_length=50)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class DataQualityIssue(SQLModel, table=True):
    __tablename__ = "data_quality_issue"
    __table_args__ = (
        Index("ix_quality_issue_batch_row", "batch_id", "row_number"),
        Index("ix_quality_issue_batch_type", "batch_id", "issue_type"),
        Index("ix_quality_issue_batch_severity", "batch_id", "severity"),
    )

    id: int | None = Field(default=None, primary_key=True)
    batch_id: uuid.UUID = Field(
        foreign_key="import_batch.id", nullable=False, ondelete="CASCADE"
    )
    row_number: int = Field(ge=2)
    issue_type: DataQualityIssueType = Field(
        sa_type=Enum(  # type: ignore
            DataQualityIssueType,
            name="data_quality_issue_type",
            native_enum=False,
            length=30,
            values_callable=lambda values: [item.value for item in values],
        )
    )
    severity: DataQualitySeverity = Field(
        sa_type=Enum(  # type: ignore
            DataQualitySeverity,
            name="data_quality_severity",
            native_enum=False,
            values_callable=lambda values: [item.value for item in values],
        )
    )
    field_name: str | None = Field(default=None, max_length=255)
    raw_value: str | None = Field(default=None, max_length=500)
    message: str = Field(max_length=1000)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class DataQualityIssuePublic(SQLModel):
    id: int
    batch_id: uuid.UUID
    row_number: int
    issue_type: DataQualityIssueType
    severity: DataQualitySeverity
    field_name: str | None
    raw_value: str | None
    message: str
    created_at: datetime


class DataQualityIssuesPublic(SQLModel):
    data: list[DataQualityIssuePublic]
    count: int


class DataQualityIssueSummaryItem(SQLModel):
    issue_type: DataQualityIssueType
    severity: DataQualitySeverity
    count: int


class DataQualityIssueSummaryPublic(SQLModel):
    data: list[DataQualityIssueSummaryItem]


class TagSample(SQLModel, table=True):
    __tablename__ = "tag_sample"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "tag_id",
            "source_timestamp",
            name="uq_tag_sample_source",
        ),
        Index("ix_tag_sample_tag_time", "tag_id", "source_timestamp"),
        Index("ix_tag_sample_task_received", "task_id", "received_at"),
        Index(
            "ix_tag_sample_task_tag_latest",
            "task_id",
            "tag_id",
            text("source_timestamp DESC"),
            text("id DESC"),
        ),
        Index("ix_tag_sample_import_batch", "import_batch_id", "id"),
        Index(
            "uq_tag_sample_import_tag_time",
            "tag_id",
            "source_timestamp",
            unique=True,
            postgresql_where=text("import_batch_id IS NOT NULL"),
        ),
        CheckConstraint(
            "(source_type = 'opcua' AND task_id IS NOT NULL AND "
            "import_batch_id IS NULL) OR "
            "(source_type = 'file' AND task_id IS NULL AND "
            "import_batch_id IS NOT NULL)",
            name="ck_tag_sample_source_reference",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    task_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="acquisition_task.id",
        ondelete="RESTRICT",
    )
    import_batch_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="import_batch.id",
        ondelete="RESTRICT",
    )
    source_type: SampleSourceType = Field(
        default=SampleSourceType.OPCUA,
        sa_type=Enum(  # type: ignore
            SampleSourceType,
            name="sample_source_type",
            native_enum=False,
            values_callable=lambda values: [item.value for item in values],
        ),
    )
    tag_id: uuid.UUID = Field(foreign_key="tag.id", nullable=False, ondelete="RESTRICT")
    value: Any = Field(sa_type=JSON)
    numeric_value: float | None = None
    source_timestamp: datetime = Field(sa_type=DateTime(timezone=True))  # type: ignore
    server_timestamp: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    received_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    status_code: str = Field(max_length=64)
    is_good: bool


class TagSamplePublic(SQLModel):
    id: int
    task_id: uuid.UUID | None
    import_batch_id: uuid.UUID | None
    source_type: SampleSourceType
    tag_id: uuid.UUID
    value: Any
    numeric_value: float | None
    source_timestamp: datetime
    server_timestamp: datetime | None
    received_at: datetime
    status_code: str
    is_good: bool


class TagSamplesPublic(SQLModel):
    data: list[TagSamplePublic]
    count: int


class LatestTagValuePublic(SQLModel):
    node_id: str
    tag_id: uuid.UUID
    tag_code: str
    tag_name: str
    data_type: TagDataType
    unit: str | None
    sampling_interval_ms: int
    sample_id: int | None = None
    value: Any | None = None
    numeric_value: float | None = None
    source_timestamp: datetime | None = None
    server_timestamp: datetime | None = None
    received_at: datetime | None = None
    status_code: str | None = None
    is_good: bool | None = None


class LatestTagValuesPublic(SQLModel):
    data: list[LatestTagValuePublic]
    count: int
    generated_at: datetime


class KnowledgeDocument(SQLModel, table=True):
    __tablename__ = "knowledge_document"
    __table_args__ = (Index("ix_knowledge_document_plant", "plant_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plant_id: uuid.UUID = Field(foreign_key="plant.id", ondelete="RESTRICT")
    title: str = Field(max_length=200)
    content: str
    version: int = 1
    content_sha256: str = Field(max_length=64)
    chunks: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)
    embedding_model: str | None = Field(default=None, max_length=200)
    created_by_id: uuid.UUID = Field(foreign_key="user.id", ondelete="RESTRICT")
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class AssistantRun(SQLModel, table=True):
    __tablename__ = "assistant_run"
    __table_args__ = (Index("ix_assistant_run_user_created", "user_id", "created_at"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", ondelete="RESTRICT")
    plant_id: uuid.UUID = Field(foreign_key="plant.id", ondelete="RESTRICT")
    question_sha256: str = Field(max_length=64)
    status: str = Field(default="running", max_length=30)
    model: str = Field(max_length=100)
    retrieval_mode: str = Field(default="lexical", max_length=30)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    tool_names: list[str] = Field(default_factory=list, sa_type=JSON)
    evidence_ids: list[str] = Field(default_factory=list, sa_type=JSON)
    error_code: str | None = Field(default=None, max_length=50)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class UserPlantAccess(SQLModel, table=True):
    __tablename__ = "user_plant_access"

    user_id: uuid.UUID = Field(
        foreign_key="user.id", primary_key=True, ondelete="CASCADE"
    )
    plant_id: uuid.UUID = Field(
        foreign_key="plant.id", primary_key=True, ondelete="CASCADE"
    )
    assigned_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    user: User | None = Relationship(back_populates="plant_accesses")
    plant: Plant | None = Relationship(back_populates="user_accesses")


class PlantAccessUpdate(SQLModel):
    plant_ids: list[uuid.UUID]


class RetentionPreview(SQLModel, table=True):
    """Audited, bounded observation only; never an approval to delete samples."""

    __tablename__ = "retention_preview"
    __table_args__ = (
        Index("ix_retention_preview_plant_created", "plant_id", "created_at"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plant_id: uuid.UUID = Field(foreign_key="plant.id", ondelete="RESTRICT")
    requested_by_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    source_type: str = Field(max_length=10)
    keep_days: int
    cutoff: datetime = Field(sa_type=DateTime(timezone=True))  # type: ignore
    high_water_id: int
    matched_rows: int
    count_is_exact: bool
    scan_limit: int
    affected_tags_in_scan: int
    oldest_in_scan: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    newest_in_scan: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class PlantAccessPublic(SQLModel):
    user_id: uuid.UUID
    plant_ids: list[uuid.UUID]


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)
