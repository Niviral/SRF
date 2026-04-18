from __future__ import annotations

from typing import Optional

import yaml
from pydantic import BaseModel, Field


class MainConfig(BaseModel):
    tenant_id: str
    master_client_id: str
    master_keyvault_id: str
    master_keyvault_secret_name: str


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
    app_id: str
    keyvault_id: str
    keyvault_secret_name: str
    keyvault_secret_description: Optional[str] = Field(default=None)


class AppConfig(BaseModel):
    main: MainConfig
    mail: Optional[MailConfig] = Field(default=None)
    secrets: list[SecretConfig]


def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return AppConfig.model_validate(raw)
