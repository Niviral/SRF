# GitHub Copilot Instructions

## Project overview

SRF is a Python CLI tool that automates rotation of Azure Service Principal (SP) client secrets and verifies SP application owners, driven by a YAML configuration file (`input.yaml`).

It calls the Microsoft Graph API (via `msgraph-sdk`) and stores rotated secrets in Azure Key Vault.

---

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.12+ |
| Config validation | Pydantic v2 (`BaseModel`, `model_validator`) |
| Azure auth | `azure-identity` (`ClientSecretCredential`, `DefaultAzureCredential`) |
| Graph API | `msgraph-sdk` (async SDK wrapped in per-call event loops) |
| Key Vault | `azure-keyvault-secrets` |
| YAML | `pyyaml` (`yaml.safe_load`) |
| Packaging | Poetry 2.x with PEP 735 `[dependency-groups]` |
| Tests | `pytest` + `pytest-mock` |

---

## Architecture

```
main.py                         CLI entry point (argparse)
srf/
├── config/models.py            Pydantic models + YAML loader
├── auth/provider.py            AuthProvider — builds credential for master SP
├── keyvault/client.py          KeyVaultClient, parse_keyvault_uri()
├── graph/client.py             GraphClient — wraps async msgraph-sdk calls
├── rotation/rotator.py         SecretRotator, RotationResult
├── ownership/checker.py        OwnershipChecker, OwnershipResult
├── runner/parallel.py          ParallelRunner — ThreadPoolExecutor across all SPs
└── reporting/mail.py           MailReporter — HTML + plain-text email via SMTP
tests/
├── conftest.py                 Shared pytest fixtures (no real Azure calls)
├── test_config.py
├── test_keyvault_client.py
├── test_graph_client.py
├── test_rotator.py
├── test_ownership.py
├── test_runner.py
└── test_mail.py
```

---

## Coding conventions

- Always add `from __future__ import annotations` at the top of every module.
- Use `dataclass` for plain result types (`RotationResult`, `OwnershipResult`).
- Use Pydantic `BaseModel` for configuration/input models only.
- Prefer `Optional[X]` with an explicit `Field(default=None)` over bare `X | None`.
- Worker classes receive all dependencies via `__init__` (dependency injection). Never instantiate Azure SDK clients inside business logic methods.
- Each public class gets a one-line docstring. Internal helpers use `# ----` section dividers.
- `GraphClient` wraps every async SDK call in a private `_run()` method that creates and tears down an isolated event loop — preserve this pattern for any new Graph operations.
- Never log or include `str(exc)` for operations that touch secrets. Use `type(exc).__name__` only, and add a note to check Azure logs.

---

## Security rules

- **Never** log secret values, token strings, or request bodies that may contain credentials.
- Error messages for secret operations must use `type(exc).__name__` only — not `str(exc)`.
- Do not store credentials in source files, `.env` files committed to the repo, or GitHub Actions workflow files. Use environment variables, GitHub Secrets, OIDC, or Key Vault.

---

## Testing conventions

- All Azure SDK calls must be monkeypatched — no real credentials or network calls in unit tests.
- Place shared fixtures in `tests/conftest.py`.
- Use the constants defined at the top of `conftest.py` (`TENANT_ID`, `MASTER_CLIENT_ID`, `SP_KV_ID`, etc.) rather than hardcoding strings in individual test files.
- Test files are named `test_<module>.py` matching the `srf/` module they cover.
- E2E tests live in `tests/test_e2e.py` and require real Azure credentials; they are excluded from CI unit test runs (`--ignore=tests/test_e2e.py`).
- Run unit tests: `poetry run pytest tests/ -v -W ignore::DeprecationWarning --tb=short --ignore=tests/test_e2e.py`

---

## Adding a new action type

1. Create a new module under `srf/` (e.g. `srf/connections/updater.py`).
2. Define a `@dataclass` result type and a worker class with a `process(secret_config: SecretConfig)` method.
3. Inject any required clients via `__init__`.
4. Pass the new worker to `ParallelRunner` (or create a second runner).
5. Wire construction and result printing in `main.py`.
6. No changes to existing modules are required.

---

## Git & PR conventions

- Branches: `feature/<desc>`, `fix/<desc>`, `chore/<desc>`
- Commits: [Conventional Commits](https://www.conventionalcommits.org/) — `feat:`, `fix:`, `chore:`, `style:`, `test:`, `docs:`
- One feature/fix per PR; all CI checks must be green before merging.
- Target branch: `master`
- Don't add copilot as Co-Author in commit messages — the commit history should reflect human authorship only.
- **Before starting any new feature or fix, always create a dedicated branch** (`git checkout -b feature/<desc>`) and verify you are on that branch before making any commits.

---

## Running locally

```bash
# Install all deps (including dev group)
poetry install --with dev

# Run the tool
poetry run python main.py

# Run unit tests
poetry run pytest tests/ -v -W ignore::DeprecationWarning --tb=short --ignore=tests/test_e2e.py
```
