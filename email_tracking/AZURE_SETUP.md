# Azure Setup Instructions — for the server person

This folder contains the email tracking backend. When deployed, it records every email open and link click in Cosmos DB. The Google Sheets pull tracking data back via API calls.

## What to deploy

Only these three files are needed on Azure:

| File | Purpose |
|---|---|
| `function_app.py` | All 6 API endpoints (track open, track click, analytics, dashboard, sheet sync, email detail) |
| `requirements.txt` | Python dependencies: `azure-functions`, `azure-cosmos` |
| `host.json` | Runtime config: 30s timeout, HTTP route prefix empty, 100 concurrent requests |

## Step 1: Create Azure resources

```bash
az login

# Resource group
az group create --name speakhire-tracking --location eastus

# Cosmos DB — serverless (pay per request, ~$0-1/month at this volume)
az cosmosdb create \
  --name speakhire-tracking-db \
  --resource-group speakhire-tracking \
  --capabilities EnableServerless \
  --default-consistency-level Session

az cosmosdb sql database create \
  --account-name speakhire-tracking-db \
  --resource-group speakhire-tracking \
  --name tracking

az cosmosdb sql container create \
  --account-name speakhire-tracking-db \
  --resource-group speakhire-tracking \
  --database-name tracking \
  --name events \
  --partition-key-path "/partition_key"

# Get connection string
az cosmosdb keys list \
  --name speakhire-tracking-db \
  --resource-group speakhire-tracking \
  --type connection-strings \
  --query "connectionStrings[0].connectionString"
# SAVE THIS — you'll need it in step 3

# Storage account (needed by Function App)
az storage account create \
  --name speakhiretrackstore \
  --resource-group speakhire-tracking \
  --sku Standard_LRS

# Function App — Python 3.11, Linux, Consumption plan
az functionapp create \
  --name speakhire-tracker \
  --resource-group speakhire-tracking \
  --storage-account speakhiretrackstore \
  --consumption-plan-location eastus \
  --runtime python \
  --runtime-version 3.11 \
  --functions-version 4 \
  --os-type Linux
```

## Step 2: Generate API key

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# SAVE THIS — the Google Sheets need it to call the analytics endpoints
```

## Step 3: Set environment variables

```bash
az functionapp config appsettings set \
  --name speakhire-tracker \
  --resource-group speakhire-tracking \
  --settings \
    COSMOS_CONNECTION_STRING="<connection string from step 1>" \
    TRACKING_API_KEY="<generated key from step 2>"
```

## Step 4: Deploy the code

```bash
cd email_tracking
pip install -r requirements.txt --target .python_packages/lib/site-packages
func azure functionapp publish speakhire-tracker --python
```

## Step 5: Verify deployment

```bash
# Test tracking pixel (should return 43-byte GIF, HTTP 200)
curl -I "https://speakhire-tracker.azurewebsites.net/api/o/test123"

# Test dashboard (use the actual API key)
curl "https://speakhire-tracker.azurewebsites.net/api/dashboard?api_key=YOUR_KEY"
```

Should return JSON with `{"totals": {"opens": 0, ...}}`.

## Step 6: Optional — auto-delete old events

Portal → Cosmos DB → Data Explorer → events container → Scale & Settings → Time to Live → `31536000` (1 year)

## What to send back

After setup, I need two values:

| Value | Where it goes |
|---|---|
| Function URL: `https://speakhire-tracker.azurewebsites.net` | `TRACKING_BASE_URL` in every `*_send.js` file |
| API key | `TRACKING_API_KEY` in `sheet_addons.js` and `dashboard.js` |

## API endpoints

| Endpoint | Auth | What it does |
|---|---|---|
| `GET /api/o/{id}` | Public | Returns 1×1 GIF, records open event |
| `GET /api/c/{id}` | Public | 302 redirects to real URL, records click |
| `GET /api/sheet/{campaign}?emails=...` | `?api_key=` | Per-email open/click counts for sheet sync |
| `GET /api/dashboard` | `?api_key=` | Aggregate stats across all campaigns |
| `GET /api/analytics/{campaign}` | `?api_key=` | Single-campaign breakdown |
| `GET /api/email/{email}` | `?api_key=` | All events for one recipient |

## Cost

- Cosmos DB serverless: under $1/month at thousands of events
- Functions consumption plan: free for first 1M requests/month
- **Total: effectively free**
