from __future__ import annotations

from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

ValidityDays = Literal[90, 180, 365]


class MainConfig(BaseModel):
    tenant_id: str
    master_client_id: Optional[str] = Field(default=None)
    master_keyvault_id: Optional[str] = Field(default=None)
    master_secret_name: Optional[str] = Field(default=None)
    threshold_days: int = Field(default=7, ge=0, le=365)
    validity_days: ValidityDays = Field(default=365)
    master_owners: list[str] = Field(default_factory=list)
    cleanup_old_secrets: bool = Field(default=False)

    @model_validator(mode="after")
    def _validity_exceeds_threshold(self) -> "MainConfig":
        if self.validity_days <= self.threshold_days:
            raise ValueError(
                f"validity_days ({self.validity_days}) must be greater than "
                f"threshold_days ({self.threshold_days})"
            )
        return self


class MailConfig(BaseModel):
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_password_keyvault_id: str
    smtp_password_secret_name: str
    from_address: str
    to_addresses: list[str]


class SecretConfig(BaseModel):
    name: str
    obj_id: str
    keyvault_id: str
    secret_name: str
    keyvault_secret_description: Optional[str] = Field(default=None)
    required_owners: list[str] = Field(default_factory=list)
    threshold_days: Optional[int] = Field(default=None, ge=0, le=365)
    validity_days: Optional[ValidityDays] = Field(default=None)

    @model_validator(mode="after")
    def _validity_exceeds_threshold(self) -> "SecretConfig":
        if self.validity_days is not None and self.threshold_days is not None:
            if self.validity_days <= self.threshold_days:
                raise ValueError(
                    f"validity_days ({self.validity_days}) must be greater than "
                    f"threshold_days ({self.threshold_days})"
                )
        return self


class AppConfig(BaseModel):
    main: MainConfig
    mail: Optional[MailConfig] = Field(default=None)
    secrets: list[SecretConfig]


def generate_schema() -> dict:
    """Return the JSON Schema for AppConfig (useful for YAML validation and IDE support)."""
    return AppConfig.model_json_schema()


def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return AppConfig.model_validate(raw)
