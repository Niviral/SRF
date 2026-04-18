# SRF — Service Principal Secret Rotation Framework

A Python CLI tool that automates rotation of Azure Service Principal (SP) client secrets and verification of SP application owners, driven entirely by a YAML configuration file.

---

## Features

- **Secret rotation** — detects secrets expiring within a configurable threshold (default: 7 days) and rotates them automatically via the Microsoft Graph API
- **Key Vault storage** — saves each new secret value to a per-SP Azure Key Vault (referenced by ARM resource ID)
- **Owner verification** — ensures specified Azure AD users are owners of each SP application registration; adds any that are missing (never removes)
- **Global master owners** — a single list of owners applied to *every* SP, merged with per-SP `required_owners`
- **Email report** — sends an HTML + plain-text summary email after each run (optional)
- **Parallel execution** — rotation and ownership checks run concurrently across all SPs
- **Composition-style design** — each component is injected as a dependency, making it easy to extend (e.g. add Service Connection rotation)

---

## Architecture

```
main.py                    CLI entry point
srf/
├── config/models.py       Pydantic v2 config models + YAML loader
├── auth/provider.py       Bootstrap auth → ClientSecretCredential for master SP
├── keyvault/client.py     Azure Key Vault secret read/write
├── graph/client.py        Microsoft Graph API (password creds + owners)
├── rotation/rotator.py    Expiry check + rotation logic
├── ownership/checker.py   Owner diff + add-missing logic
├── runner/parallel.py     ThreadPoolExecutor — runs all SPs in parallel
└── reporting/mail.py      HTML + plain-text email report via SMTP
```

---

## Prerequisites

### Python
Python 3.12 or later.

### Master Service Principal permissions

The master SP (whose credentials bootstrap the tool) requires the following **Microsoft Graph application permission**:

| Permission | Why |
|---|---|
| `Application.ReadWrite.OwnedBy` | Read/rotate secrets on SPs it owns |

> The master SP must be set as an **owner** of each SP it will manage.

For owner verification (`required_owners` / `master_owners`), the master SP additionally needs:

| Permission | Why |
|---|---|
| `Application.ReadWrite.OwnedBy` | List and add owners |

---

## Installation

### With Poetry (recommended)

```bash
# Install dependencies
poetry install

# Run the tool
poetry run python main.py

# Run tests
poetry run pytest tests\ -v
```

### Without Poetry (plain pip)

```bash
python -m venv venv
# Windows
.\venv\Scripts\pip install -r requirements.txt

# Linux / macOS
venv/bin/pip install -r requirements.txt
```

---

## Authentication

The tool uses a **two-step bootstrap**:

1. `DefaultAzureCredential` reads the master SP's client secret from the configured Key Vault  
   *(works with Managed Identity, Azure CLI `az login`, environment variables, etc.)*
2. A `ClientSecretCredential` is created from the retrieved secret for all subsequent Graph and Key Vault calls

The master SP's client secret is **never stored in the YAML file** — only the Key Vault reference is.

---

## Configuration (`input.yaml`)

```yaml
main:
  tenant_id: <azure-tenant-id>
  master_client_id: <master-sp-client-id>

  # ARM resource ID of the Key Vault holding the master SP's own client secret
  master_keyvault_id: /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.KeyVault/vaults/<kv-name>
  master_keyvault_secret_name: master-sp-client-secret

  # Rotation settings (can be overridden per-run with CLI flags)
  threshold_days: 7     # rotate if secret expires within this many days
  validity_days: 365    # validity period for newly created secrets

  # Users added as owner to EVERY SP (by Azure AD user object ID)
  master_owners:
    - 00000000-0000-0000-0000-000000000001

# Optional — omit the mail block to skip email reporting
mail:
  smtp_host: smtp.example.com
  smtp_port: 587
  smtp_user: rotation-tool@example.com
  # SMTP password fetched from Key Vault at runtime
  smtp_password_keyvault_id: /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.KeyVault/vaults/<kv-name>
  smtp_password_secret_name: smtp-password
  from_address: rotation-tool@example.com
  to_addresses:
    - ops-team@example.com

secrets:
  - name: my-service-sp
    app_id: <application-client-id>   # appId (not object ID)

    # Key Vault where the rotated secret value will be stored
    keyvault_id: /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.KeyVault/vaults/<kv-name>
    keyvault_secret_name: my-service-sp-secret
    keyvault_secret_description: "Managed by SRF"   # optional, stored as KV content_type

    # Users added as owner for THIS SP only (merged with master_owners)
    required_owners:
      - 00000000-0000-0000-0000-000000000002
```

### Config reference

#### `main` section

| Field | Required | Default | Description |
|---|---|---|---|
| `tenant_id` | ✅ | — | Azure AD tenant ID |
| `master_client_id` | ✅ | — | Client ID of the master SP |
| `master_keyvault_id` | ✅ | — | ARM resource ID of the Key Vault holding the master SP secret |
| `master_keyvault_secret_name` | ✅ | — | Secret name inside that Key Vault |
| `threshold_days` | | `7` | Rotate if expiry is within this many days |
| `validity_days` | | `365` | New secret validity in days |
| `master_owners` | | `[]` | User object IDs added as owner to every SP |

#### `secrets[]` entries

| Field | Required | Default | Description |
|---|---|---|---|
| `name` | ✅ | — | Display name (used in reports) |
| `app_id` | ✅ | — | SP application (client) ID |
| `keyvault_id` | ✅ | — | ARM resource ID of the Key Vault to store the rotated secret |
| `keyvault_secret_name` | ✅ | — | Secret name to create/overwrite in that Key Vault |
| `keyvault_secret_description` | | `null` | Stored as `content_type` on the KV secret |
| `required_owners` | | `[]` | User object IDs added as owner for this SP only |

#### `mail` section (optional)

| Field | Required | Default | Description |
|---|---|---|---|
| `smtp_host` | ✅ | — | SMTP server hostname |
| `smtp_port` | | `587` | SMTP port (STARTTLS) |
| `smtp_user` | ✅ | — | SMTP login username |
| `smtp_password_keyvault_id` | ✅ | — | ARM resource ID of KV holding SMTP password |
| `smtp_password_secret_name` | ✅ | — | Secret name for SMTP password |
| `from_address` | ✅ | — | Sender email address |
| `to_addresses` | ✅ | — | List of recipient email addresses |

---

## Usage

```bash
# With Poetry
poetry run python main.py
poetry run python main.py --config /path/to/config.yaml --threshold-days 14

# With venv directly
python main.py
```

# Override rotation threshold and validity for this run
python main.py --threshold-days 14 --validity-days 180

# Increase parallelism
python main.py --workers 10
```

### CLI flags

| Flag | Default | Description |
|---|---|---|
| `--config` | `input.yaml` | Path to YAML config file |
| `--threshold-days` | YAML value (7) | Override: days before expiry to trigger rotation |
| `--validity-days` | YAML value (365) | Override: new secret validity in days |
| `--workers` | `5` | Max parallel threads |

> CLI flags always take precedence over YAML values when explicitly provided.

---

## Output

The tool prints two summary tables to stdout:

```
================================================================
Azure SP Secret Rotation — Summary
Run: 2026-04-18 08:30 UTC
----------------------------------------------------------------
NAME                 APP ID                                 STATUS                     EXPIRY                     DETAIL
----------------------------------------------------------------
my-service-sp        6302d378-...                           ✓ ROTATED                  2027-04-18 08:30 UTC       vault=my-secrets-kv
other-sp             32e99f34-...                           – SKIPPED                  2026-06-01 00:00 UTC       not expiring soon
================================================================

================================================================
Azure SP Ownership — Summary
----------------------------------------------------------------
NAME                 APP ID                                 STATUS       DETAIL
----------------------------------------------------------------
my-service-sp        6302d378-...                           ✓ UPDATED    added=['00000000-...']
other-sp             32e99f34-...                           – OK         all owners present
================================================================
```

If the `mail` block is configured, an HTML email with the same information is sent after the run.

---

## Owner verification logic

Effective owners for a given SP = `master_owners` ∪ `required_owners` (master owners first, deduplicated).

- If the effective set is **empty** → ownership check is skipped for that SP
- Owners already present → no action, reported as OK
- Missing owners → added via `POST /applications/{id}/owners/$ref`
- Owners are **never removed**

---

## Development

### Install dev dependencies

```bash
# With Poetry (recommended)
poetry install   # installs main + dev deps

# With pip
.\venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
```

### Run tests

```bash
# With Poetry
poetry run pytest tests\ -v

# With pip / venv
.\venv\Scripts\pytest tests\ -v
```

56 tests covering: config validation, Key Vault client, Graph API client, rotation expiry logic, parallel runner error isolation, ownership checker, and email report generation. All Azure SDK calls are monkeypatched — no real Azure credentials required.

### Project structure

```
srf/
├── config/models.py       AppConfig, MainConfig, MailConfig, SecretConfig
├── auth/provider.py       AuthProvider
├── keyvault/client.py     KeyVaultClient, parse_keyvault_uri()
├── graph/client.py        GraphClient
├── rotation/rotator.py    SecretRotator, RotationResult
├── ownership/checker.py   OwnershipChecker, OwnershipResult
├── runner/parallel.py     ParallelRunner
└── reporting/mail.py      MailReporter
tests/
├── conftest.py
├── test_config.py
├── test_keyvault_client.py
├── test_graph_client.py
├── test_rotator.py
├── test_ownership.py
├── test_runner.py
└── test_mail.py
```

---

## Extending the tool

The composition-style design makes it straightforward to add new features:

1. **New action type** (e.g. Service Connection update): create a new module under `srf/`, define a `Result` dataclass and a worker class with a single `process(secret_config)` method
2. **Plug into runner**: pass the new worker to `ParallelRunner` (or create a second runner)
3. **Wire in `main.py`**: add the worker construction and result printing

No changes to existing modules are required.
