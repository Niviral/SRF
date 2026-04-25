# Azure Debugging & Monitoring Snippets

Useful CLI commands and Azure Resource Graph queries for inspecting the Azure resources managed by AzureSecretRotationFramework.

---

## Contents

- [Azure Resource Graph Queries](#azure-resource-graph-queries)
  - [List all Key Vaults](#list-all-key-vaults)
  - [List all App Registrations (Service Principals)](#list-all-app-registrations-service-principals)
- [Azure CLI — Key Vault](#azure-cli--key-vault)
  - [List secrets in a vault](#list-secrets-in-a-vault)
  - [Show a secret's metadata](#show-a-secrets-metadata)
  - [Show secret expiry](#show-secret-expiry)
  - [Get current secret value](#get-current-secret-value)
- [Azure CLI — App Registrations & Service Principals](#azure-cli--app-registrations--service-principals)
  - [List all password credentials on an app](#list-all-password-credentials-on-an-app)
  - [Show expiring credentials (within N days)](#show-expiring-credentials-within-n-days)
  - [List owners of an app registration](#list-owners-of-an-app-registration)
  - [Show SP details by app ID](#show-sp-details-by-app-id)
- [Azure CLI — Audit & Diagnostics](#azure-cli--audit--diagnostics)
  - [Show recent Key Vault audit events](#show-recent-key-vault-audit-events)
  - [Check current signed-in identity](#check-current-signed-in-identity)
  - [Verify master SP permissions](#verify-master-sp-permissions)

---

## Azure Resource Graph Queries

Run these in the [Azure Resource Graph Explorer](https://portal.azure.com/#view/HubsExtension/ArgQueryBlade) or via CLI:

```bash
az graph query -q '<query>'
```

### List all Key Vaults

```kusto
resources
| where type =~ 'microsoft.keyvault/vaults'
| extend
    vaultUri = tostring(properties.vaultUri),
    tenant = tostring(tenantId),
    enableSoftDelete = tostring(properties.enableSoftDelete),
    enablePurgeProtection = tostring(properties.enablePurgeProtection),
    enabledForDeployment = tostring(properties.enabledForDeployment),
    skuName = tostring(sku.name)
| project name, id, location, resourceGroup, vaultUri, skuName, tenant,
    enableSoftDelete, enablePurgeProtection, enabledForDeployment, tags
| order by name asc
| take 500
```

### List all App Registrations (Service Principals)

```kusto
resources
| where type =~ 'microsoft.aad/serviceprincipals'
| extend spType = tostring(properties.servicePrincipalType)
| project name, id, location, resourceGroup, spType, tags
| order by name asc
| take 500
```

---

## Azure CLI — Key Vault

### List secrets in a vault

```bash
az keyvault secret list \
  --vault-name <vault-name> \
  --query "[].{name:name, enabled:attributes.enabled, expires:attributes.expires, created:attributes.created}" \
  --output table
```

### Show a secret's metadata

```bash
az keyvault secret show \
  --vault-name <vault-name> \
  --name <secret-name> \
  --query "{name:name, enabled:attributes.enabled, expires:attributes.expires, contentType:contentType, updated:attributes.updated}" \
  --output table
```

### Show secret expiry

```bash
az keyvault secret show \
  --vault-name <vault-name> \
  --name <secret-name> \
  --query "attributes.expires" \
  --output tsv
```

### Get current secret value

> ⚠️ Treat output as sensitive — do not log or share.

```bash
az keyvault secret show \
  --vault-name <vault-name> \
  --name <secret-name> \
  --query "value" \
  --output tsv
```

---

## Azure CLI — App Registrations & Service Principals

### List all password credentials on an app

Shows all client secrets attached to an app registration (values are not retrievable — only metadata).

```bash
az ad app credential list \
  --id <app-id> \
  --query "[].{keyId:keyId, displayName:displayName, startDate:startDateTime, endDate:endDateTime, hint:hint}" \
  --output table
```

### Show expiring credentials (within N days)

```bash
# Set your threshold
DAYS=30
CUTOFF=$(date -u -d "+${DAYS} days" +"%Y-%m-%dT%H:%M:%SZ")

az ad app list --all \
  --query "[].{appId:appId, displayName:displayName, creds:passwordCredentials}" \
  --output json | python3 -c "
import json, sys
from datetime import datetime, timezone
cutoff = datetime.fromisoformat('${CUTOFF}'.replace('Z','+00:00'))
apps = json.load(sys.stdin)
for app in apps:
    for cred in (app.get('creds') or []):
        end = cred.get('endDateTime')
        if end:
            exp = datetime.fromisoformat(end.replace('Z','+00:00'))
            if exp <= cutoff:
                print(f\"{app['displayName']:40} {app['appId']}  expires={end}  hint={cred.get('hint','')}\")
"
```

### List owners of an app registration

```bash
az ad app owner list \
  --id <app-id> \
  --query "[].{displayName:displayName, objectId:id, userPrincipalName:userPrincipalName}" \
  --output table
```

### Show SP details by app ID

```bash
az ad sp show \
  --id <app-id> \
  --query "{displayName:displayName, appId:appId, objectId:id, servicePrincipalType:servicePrincipalType}" \
  --output table
```

---

## Azure CLI — Audit & Diagnostics

### Show recent Key Vault audit events

Requires diagnostic logs sent to Log Analytics. Run in the Log Analytics workspace:

```kusto
AzureDiagnostics
| where ResourceProvider == "MICROSOFT.KEYVAULT"
| where OperationName in ("SecretSet", "SecretGet", "SecretDelete")
| project TimeGenerated, OperationName, ResultType, CallerIPAddress,
    identity_claim_appid_g, Resource, requestUri_s
| order by TimeGenerated desc
| take 200
```

### Check current signed-in identity

```bash
az account show \
  --query "{name:name, tenantId:tenantId, user:user}" \
  --output table
```

### Verify master SP permissions

Check what Graph API permissions the master SP has been granted:

```bash
# List app role assignments (application permissions)
az ad sp show --id <master-sp-app-id> --query "appRoles" --output table

# List OAuth2 permissions granted (delegated)
az rest \
  --method GET \
  --url "https://graph.microsoft.com/v1.0/servicePrincipals/<master-sp-object-id>/appRoleAssignments" \
  --query "value[].{role:appRoleId, resourceId:resourceId, principalId:principalId}" \
  --output table
```
