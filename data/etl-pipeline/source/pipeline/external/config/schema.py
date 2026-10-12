import logging
import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel

from shared.config_utils import resolve_dict as _resolve_dict

log = logging.getLogger(__name__)


class AuthConfig(BaseModel):
    type: Literal["oauth2", "api_key", "basic", "none"] = "none"
    token_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    scopes: list[str] = []
    api_key_header: str = "X-API-Key"
    api_key_value: str = ""


class PollConfig(BaseModel):
    schedule: str = "0 * * * *"
    base_url: str = ""
    cursor_param: str = "updated_since"
    incremental_field: str = ""
    cursor_strategy: Literal["timestamp", "offset", "page"] = "timestamp"
    page_size: int = 100
    page_param: str = "page"
    page_size_param: str = "limit"
    results_field: str = "results"
    next_page_field: str = ""
    max_pages: int = 500
    initial_cursor: str = "1970-01-01T00:00:00"
    request_timeout_s: int = 30


class FileConfig(BaseModel):
    landing_container: str = "external-landing"
    landing_prefix: str = ""
    formats: list[str] = ["csv", "json"]
    delimiter: str = ","
    has_header: bool = True
    sheet_name: Optional[str] = None


class DbConfig(BaseModel):
    driver: str = "postgresql+psycopg2"
    host: str = ""
    port: int = 5432
    database: str = ""
    username: str = ""
    password: str = ""
    query: str = ""
    watermark_field: str = ""
    watermark_strategy: Literal["timestamp", "offset"] = "timestamp"
    initial_watermark: str = "1970-01-01T00:00:00"
    chunk_size: int = 1000


class RoutingConfig(BaseModel):
    strategy: Literal["kafka", "iceberg", "auto"] = "kafka"
    kafka_threshold_rows: int = 10000
    batch_flush_size: int = 500


class ConnectorConfig(BaseModel):
    source_name: str
    source_entity: str
    connector_type: Literal["rest_api", "file", "db"]
    tenant_id: str = ""
    id_field: str = "id"
    ts_field: str = ""
    auth: AuthConfig = AuthConfig()
    poll: PollConfig = PollConfig()
    file: FileConfig = FileConfig()
    db: DbConfig = DbConfig()
    routing: RoutingConfig = RoutingConfig()

    @property
    def resolved_tenantid(self) -> str:
        return self.tenant_id or os.environ.get("TENANTID", "")


def load_config(path: "str | Path", tenant_id: str = "") -> ConnectorConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    resolved = _resolve_dict(raw)
    cfg = ConnectorConfig(**resolved)
    if tenant_id:
        cfg = cfg.model_copy(update={"tenant_id": tenant_id})
    return cfg


def load_configs_dir(directory: "str | Path") -> list[ConnectorConfig]:
    configs = []
    for p in sorted(Path(directory).glob("*.yaml")):
        if p.stem.startswith("example_"):
            continue
        try:
            configs.append(load_config(p))
        except Exception as exc:
            log.warning("Skipping config %s: %s", p.name, exc)
    return configs
