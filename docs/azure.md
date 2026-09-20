# Optional Azure archive

RinkCheck runs locally with React, FastAPI, and SQLite. No Azure resource has been provisioned by this repository, and the web app has not been deployed to Azure. The implemented integration is a separate CLI that verifies a reviewed export and uploads its evidence bundle to Azure Blob Storage. Local development and the demo never require Azure credentials.

## What the adapter does

1. Requests `/api/runs/{run_id}/export`. The API refuses runs with unresolved cases.
2. Bounds compressed and uncompressed size, rejects redirects, and checks that the ZIP contains exactly the five expected files without duplicate entries.
3. Verifies run ID, rules version, resolved-state and coverage counts, and every payload file's SHA-256 against `manifest.json`. Excluded games remain explicit; an export with exclusions is not labeled complete.
4. Uploads the ZIP to `exports/{run_id}/{sha256-of-entire-zip}.zip` with checksum, run ID, and rules version metadata. It never overwrites an object. If that exact object already exists, the adapter verifies its actual bytes before reporting success.

Internal checksums detect corruption or inconsistent content; they are not a signature proving who produced the export. Azure archiving also does not make a local self-reported reviewer name an authenticated identity. The ZIP preserves raw source files, which may contain spreadsheet formulas: inspect raw evidence in a text editor, and use `canonical.csv` for spreadsheet work.

Each export contains its export time, so downloading the same run later may create a different ZIP and therefore a different content address. Content addressing deduplicates identical bytes, not semantically equivalent runs. This is deliberate snapshot behavior.

The integration uses `DefaultAzureCredential` and the Azure Blob Python SDK. A signed-in Azure CLI identity can supply credentials locally; a later hosted worker can use managed identity. The documentation explains how the credential chain chooses an identity. [Microsoft credential-chain reference](https://learn.microsoft.com/en-us/azure/developer/python/sdk/authentication/credential-chains).

## First verify without cloud access

Start RinkCheck using the README instructions. Review all cases in a run, or load the clean demo. Find the run ID with:

```bash
curl --fail http://127.0.0.1:8000/api/runs
```

Then substitute that ID:

```bash
python scripts/archive_to_azure.py --run-id YOUR_RUN_ID --dry-run
```

This mode neither imports the Azure SDK nor attempts authentication. It prints the bundle checksum, byte length, rules version, and proposed object name. All review and export gates still apply.

## Connect your own Azure account later

The following are commands for you to run after deciding to connect Azure. They create resources that can incur charges; they have not been executed as part of building this project. Use a dedicated demo resource group and subscription, and set a budget in the portal before provisioning. No storage keys, connection strings, or service-principal secrets belong in this repo.

1. Install the Azure CLI and the optional Python dependencies in your project virtual environment, then sign in and select the intended subscription:

   ```bash
   python -m pip install -r backend/requirements-azure.txt
   az login
   az account list --output table
   az account set --subscription YOUR_SUBSCRIPTION_ID
   az account show --query '{name:name,id:id,tenant:tenantId}' --output table
   ```

2. Choose a globally unique storage account name containing only lowercase letters and digits, 3–24 characters. Change the example values before running:

   ```bash
   RINKCHECK_RG='rg-rinkcheck-demo'
   RINKCHECK_REGION='eastus'
   RINKCHECK_STORAGE='replacewithuniquename'
   RINKCHECK_CONTAINER='rinkcheck-exports'

   az group create --name "$RINKCHECK_RG" --location "$RINKCHECK_REGION"
   az storage account create \
     --name "$RINKCHECK_STORAGE" \
     --resource-group "$RINKCHECK_RG" \
     --location "$RINKCHECK_REGION" \
     --kind StorageV2 \
     --sku Standard_LRS \
     --https-only true \
     --min-tls-version TLS1_2 \
     --allow-blob-public-access false \
     --allow-shared-key-access false
   ```

   This creates an authenticated public network endpoint, not a private endpoint. Anonymous blob access and shared-key authorization are disabled. A real deployment should choose its network access and redundancy policy based on its requirements. [Microsoft storage-account creation guide](https://learn.microsoft.com/en-us/azure/storage/common/storage-account-create).

3. Grant your signed-in user data access on this dedicated account. Creating role assignments requires permission to manage Azure RBAC. If your account lacks it, the subscription administrator must make the assignment:

   ```bash
   RINKCHECK_STORAGE_ID=$(az storage account show \
     --name "$RINKCHECK_STORAGE" --resource-group "$RINKCHECK_RG" \
     --query id --output tsv)
   RINKCHECK_PRINCIPAL_ID=$(az ad signed-in-user show --query id --output tsv)

   az role assignment create \
     --assignee-object-id "$RINKCHECK_PRINCIPAL_ID" \
     --assignee-principal-type User \
     --role 'Storage Blob Data Contributor' \
     --scope "$RINKCHECK_STORAGE_ID"
   ```

   The account-level role is scoped to this demo account. For a shared account, have an administrator create the container and grant the app access at that container's scope instead. RBAC changes can take time to propagate. [Microsoft Blob data-access guidance](https://learn.microsoft.com/en-us/azure/storage/blobs/assign-azure-role-data-access).

4. Create the private container using your Entra identity, then run the archive adapter:

   ```bash
   az storage container create \
     --account-name "$RINKCHECK_STORAGE" \
     --name "$RINKCHECK_CONTAINER" \
     --auth-mode login \
     --public-access off

   AZURE_TOKEN_CREDENTIALS=AzureCliCredential python scripts/archive_to_azure.py \
     --run-id YOUR_RUN_ID \
     --account-url "https://${RINKCHECK_STORAGE}.blob.core.windows.net" \
     --container "$RINKCHECK_CONTAINER"
   ```

   Setting `AZURE_TOKEN_CREDENTIALS` to `AzureCliCredential` explicitly selects the CLI identity for this command. The script supports public-Azure `*.blob.core.windows.net` endpoints only. Sovereign clouds and emulators require a deliberate endpoint-policy extension. [Azure CLI container reference](https://learn.microsoft.com/en-us/cli/azure/storage/container#az-storage-container-create), [Microsoft Python upload guide](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-upload-python).

5. Inspect the uploaded object without making it public:

   ```bash
   az storage blob list \
     --account-name "$RINKCHECK_STORAGE" \
     --container-name "$RINKCHECK_CONTAINER" \
     --auth-mode login \
     --query '[].{name:name,bytes:properties.contentLength}' \
     --output table
   ```

   Keep the printed checksum with your demo notes. A successful CLI response is evidence of this archive operation; it is not evidence that the web application is hosted in Azure. Delete the dedicated demo resources through the portal when finished, after keeping any exports you need.

## Hosting is a separate next step

The Docker image is designed for a single local worker. Compose binds the app to `127.0.0.1:8000`, runs as UID 10001, and stores SQLite on a named local volume. `docker compose down` preserves that volume; `docker compose down -v` deletes it. Do not mount the SQLite file on Azure Files or share it between replicas.

A credible internet-facing deployment needs additional implementation:

- Entra authentication, authorization for reviewers and administrators, and authenticated identities in the audit records. Today the reviewer field is a local operator label.
- A managed PostgreSQL persistence layer with equivalent transaction, uniqueness, optimistic-concurrency, and immutable-evidence behavior before multiple app replicas.
- A hosting service, managed identity with narrowly scoped Blob access, TLS, ingress controls, request limits, backup and restore validation, and operational telemetry.
- A defined retention policy for evidence and exports. If retention must resist administrator deletion, configure storage immutability deliberately. Content-hash object names and `overwrite=False` alone are not WORM storage.

Those are future deployment tasks, not implemented capabilities. Azure Blob currently adds a durable destination for reviewed evidence without changing the local review workflow.
