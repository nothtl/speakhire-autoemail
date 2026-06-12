# Email Tracking

Open and click tracking for all SpeakHire outreach emails. Uses Azure Functions + Cosmos DB. Data flows both ways: emails → Azure (tracking), Azure → Google Sheets (sync + dashboard).

## Files - what each one is for

| File | What it is | Deploy to |
|---|---|---|
| `function_app.py` | Azure Function - all 6 API endpoints | `func azure functionapp publish ...` |
| `requirements.txt` | Python deps for the function | Bundled with function_app.py |
| `host.json` | Azure Functions runtime settings | Bundled with function_app.py |
| `local.settings.json` | Template for local dev | Your machine only (not committed) |
| `sheet_addons.js` | Code to **paste into** each campaign send script | Google Sheets → Extensions → Apps Script |
| `dashboard.js` | Standalone script for a separate dashboard sheet | A new Google Sheet's Apps Script |
| `implementation.md` | Architecture deep-dive + Azure concepts | Your brain |
| `README.md` | This file - quick start | - |

## Quick start

### 1. Deploy the backend to Azure

```bash
cd email_tracking
pip install -r requirements.txt --target .python_packages/lib/site-packages
func azure functionapp publish speakhire-tracker --python
```

### 2. Wire tracking into your send scripts

Open each campaign's `*_send.js` file. **It already has the tracking code.** You just need to update one URL:

```javascript
var TRACKING_BASE_URL = "https://speakhire-tracker.azurewebsites.net";  // ← your real URL
```

The send scripts already include:
- `encodeTrackingId()` + `getTrackingPixel()` - embeds a tracking pixel in every sent email
- `syncTracking()` - pulls open/click counts back into the campaign sheet
- `syncDashboard()` - populates a dashboard tab with summary stats

Each campaign's menu now has three items:

| Menu item | Does |
|---|---|
| **📬 Send Batch** | Sends emails (tracking pixel auto-embedded) |
| **🔄 Sync Tracking** | Pulls open/click counts per email into the sheet |
| **📊 Sync Dashboard** | Populates a "Tracking Dashboard" tab with aggregate stats |

### 3. (Optional) Create a standalone dashboard sheet

If you want a separate spreadsheet just for the dashboard:

1. Create a new Google Sheet
2. Extensions → Apps Script → paste `dashboard.js`
3. Update `TRACKING_SYNC_URL` and `TRACKING_API_KEY`
4. Click **📊 Sync Dashboard**

The dashboard sheet talks only to Azure - it doesn't need access to your campaign sheets.

## How it works (30 seconds)

```
You send an email
  │
  └── <img src="azure-fn/api/o/encoded-id" width=1 height=1>
        │
        ▼
  Azure Function records "open" in Cosmos DB, returns 1×1 GIF
        │
        ▼
  You click "🔄 Sync Tracking" in your Google Sheet
        │
        ▼
  Sheet calls GET /api/sheet/{campaign}?emails=...
        │
        ▼
  Azure queries Cosmos DB, returns {"jane@x.com": {"opens": 3, "clicks": 1}}
        │
        ▼
  Sheet writes "3" and "1" to the Opens/Clicks columns in the row for jane@x.com
```

## API endpoints

| Endpoint | Auth | Returns |
|---|---|---|
| `GET /api/o/{id}` | Public | 1×1 tracking GIF |
| `GET /api/c/{id}` | Public | 302 redirect to real URL |
| `GET /api/sheet/{campaign}?emails=...` | `?api_key=` | Per-email open/click counts |
| `GET /api/dashboard` | `?api_key=` | All-campaign aggregate stats |
| `GET /api/analytics/{campaign}` | `?api_key=` | Campaign-level breakdown |
| `GET /api/email/{email}` | `?api_key=` | All events for one person |

## Where tracking data lives

| Location | What |
|---|---|
| **Azure Cosmos DB** (`events` container) | Every open + click event, stored as JSON |
| **Google Sheet campaign tabs** | Per-row open/click counts (synced via menu) |
| **Google Sheet "Tracking Dashboard" tab** | Aggregate summary cards + recent activity |
| **Google Sheet (standalone dashboard)** | Same as above, but in its own spreadsheet |

## Setup checklist

- [ ] `az login` + create Cosmos DB + Function App (commands in `implementation.md`)
- [ ] Set `COSMOS_CONNECTION_STRING` + `TRACKING_API_KEY` as Azure app settings
- [ ] `func azure functionapp publish`
- [ ] Update `TRACKING_BASE_URL` + `TRACKING_SYNC_URL` in `sheet_addons.js`
- [ ] Paste updated JS into each campaign sheet's Apps Script
- [ ] Paste `dashboard.js` into a new sheet (optional)
- [ ] Send a test email to yourself, open it, run Sync Tracking
