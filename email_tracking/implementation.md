# Email Tracking - Code Walkthrough & Maintenance Guide

## File dependency map

```
email_tracking/
│
├── function_app.py          ← deploy to Azure (the backend)
│   ├── imports: azure.functions, azure.cosmos
│   ├── reads:  COSMOS_CONNECTION_STRING, TRACKING_API_KEY (env vars)
│   └── exposes: 6 HTTP endpoints (track_open, track_click, sheet_sync,
│                                        dashboard, analytics, email_detail)
│
├── sheet_addons.js          ← paste INTO each campaign send script
│   ├── depends on: CAMPAIGN_SLUG, COL_EMAIL, SHEET_NAME (from parent script)
│   ├── calls:    Azure /api/sheet/{campaign} and /api/dashboard
│   └── adds:     syncTracking(), syncDashboard(), encodeTrackingId(),
│                 getTrackingPixel(), getOrCreateTrackingColumn()
│
├── dashboard.js             ← paste into a NEW standalone sheet
│   ├── depends on: nothing (fully standalone)
│   ├── calls:    Azure /api/dashboard and /api/sheet/{campaign}
│   └── creates:  "Dashboard" tab + per-campaign detail tabs
│
├── requirements.txt         ← azure-functions, azure-cosmos
├── host.json                ← Functions runtime config (concurrency, timeout, logging)
└── local.settings.json      ← template for local dev (never commit real values)
```

## End-to-end data flow: what happens when someone opens an email

```
1. You send an email via GmailApp.sendEmail()
   └── The HTML body contains:
       <img src="https://track.azurewebsites.net/api/o/eyJlIjoiamFuZUB4LmNvbSIsIm4i...">

2. Recipient opens the email
   └── Their email client (Gmail/Outlook/Apple Mail) renders the HTML
       └── It sees the <img> tag and makes a GET request to that URL

3. Azure receives the request
   └── Function App wakes up (cold start: 2-5s, warm: <100ms)
       └── Routes to track_open() because the URL matches /api/o/{email_id}

4. track_open() runs:
   ├── Extracts "eyJlIjoi..." from the URL path (email_id)
   ├── Calls _record_event("open", email_id, req)
   │   ├── _decode_email_id(email_id) → base64 decode → {"e":"jane@x.com","n":"Jane",...}
   │   ├── Builds a JSON document with all metadata
   │   └── _container.create_item(doc) → writes to Cosmos DB
   └── Returns 1×1 transparent GIF (43 bytes) with no-cache headers

5. Email client receives the 43-byte GIF
   └── Renders it as an invisible 1×1 pixel
   └── User sees nothing - just the normal email

Total time: ~100ms (warm) to ~5s (cold). The user never notices.
```

## Code walkthrough: function_app.py (521 lines)

### Imports (lines 1-12)

```python
import os           # reading env vars (COSMOS_CONNECTION_STRING, TRACKING_API_KEY)
import uuid         # generating unique event IDs
import json         # parsing/serializing JSON (request bodies, Cosmos docs)
import logging      # Azure Functions logging (appears in Portal → Logs)
import base64       # base64 encode/decode tracking IDs + the 1×1 GIF pixel
from datetime import datetime, timezone   # timestamps for events
from urllib.parse import unquote          # URL-decode redirect targets
import azure.functions as func             # Azure Functions SDK (@app.route, HttpRequest, HttpResponse)
from azure.cosmos import CosmosClient, exceptions  # Cosmos DB SDK
```

Each import serves a specific purpose. `uuid` is used once per event to generate a unique ID. `base64` is used for both the tracking pixel (decoding the GIF) AND encoding/decoding tracking IDs. `unquote` is used only in `track_click` to handle a backward-compatibility fallback.

### Module-level initialization (lines 15-30)

```python
COSMOS_CONN = os.environ["COSMOS_CONNECTION_STRING"]    # required - crashes on startup if missing
DATABASE = "tracking"
CONTAINER = "events"

_client = CosmosClient.from_connection_string(COSMOS_CONN)
_db = _client.get_database_client(DATABASE)
_container = _db.get_container_client(CONTAINER)

API_KEY = os.environ.get("TRACKING_API_KEY", "")  # optional - open access if not set

PIXEL = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)
```

**Why module-level:** In Azure Functions (Python), the module is loaded once and reused across invocations when the function is "warm." Variables defined at module scope persist between requests. This means:
- The Cosmos DB connection is established once, not per-request (saves ~200ms per request)
- The 1×1 GIF is decoded once (saves ~0.1ms - but conceptually cleaner)
- On cold start (first request after deploy or idle), everything loads fresh

The `PIXEL` variable is a base64-encoded 1×1 transparent GIF. Decoded, it's exactly 43 bytes. Every email client on the planet renders this correctly.

`API_KEY` uses `os.environ.get()` (not `os.environ[]`) so the analytics endpoints work without a key during local development.

### The route decorator (line 32)

```python
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
```

`ANONYMOUS` means no Azure Function-level auth - anyone can hit these URLs. This is intentional: tracking pixels are loaded by email clients, which can't authenticate. The sensitive endpoints (`/api/analytics`, `/api/sheet`, `/api/dashboard`, `/api/email`) do their own auth via `_check_api_key()`.

### _record_event() (line ~60)

Every tracking endpoint calls this. It's the single write path to Cosmos DB.

```python
def _record_event(event_type, email_id, req, link_url=None, link_text=None):
    try:
        meta = _decode_email_id(email_id)    # base64 → {e, n, o, c, l?, t?}
        doc = {
            "id": f"evt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
            "partition_key": f"campaign_{meta.get('c', 'unknown')}",
            "event_type": event_type,
            "email_id": email_id,
            "recipient_email": meta.get("e", ""),
            "recipient_name": meta.get("n", ""),
            "org_name": meta.get("o", ""),
            "campaign": meta.get("c", "unknown"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_agent": req.headers.get("User-Agent", "")[:500],
            "ip": req.headers.get("X-Forwarded-For", "").split(",")[0].strip() or "",
            "link_url": link_url,
            "link_text": link_text,
        }
        _container.create_item(body=doc)
    except Exception as e:
        logging.warning(f"Failed to record {event_type}: {e}")
```

**Line-by-line:**
- `try/except` wraps the entire function. If Cosmos DB is down, we log a warning and return. The tracking pixel still returns a 200 (the caller doesn't await this). This is the "fire-and-forget" pattern - the pixel response must never fail.
- `_decode_email_id(email_id)` takes the URL path segment (e.g. `eyJlIjoiamFuZUB4...`) and base64-decodes it to `{"e":"jane@x.com","n":"Jane","o":"QC House","c":"speaking_my_name"}`. This is the core design: zero database lookups to know who opened the email.
- The `id` is globally unique: timestamp + random hex. Cosmos requires a unique `id` within a partition.
- `partition_key` is `campaign_{slug}`. All events for one campaign share the same partition, making campaign-scoped queries fast and cheap.
- `user_agent` is truncated to 500 chars (some UAs are very long, we don't need the full string).
- `ip` reads from `X-Forwarded-For` (Azure's proxy header), not the raw IP (which would be Azure's internal IP). Takes only the first IP if multiple proxies are chained.

### _decode_email_id() (line ~90)

```python
def _decode_email_id(email_id):
    try:
        import json
        padded = email_id + "=" * (4 - len(email_id) % 4) if len(email_id) % 4 else email_id
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return {}
```

**Line-by-line:**
- `import json` inside the function - keeps the dependency explicit for this function (json is also imported at module level).
- `padded = ...` - base64 requires padding to multiples of 4. The JS `encodeTrackingId()` strips `=` signs to make URLs cleaner. This line adds them back before decoding. Example: `"eyJl"` (4 chars, no padding needed) vs `"eyJ"` (3 chars, needs 1 `=` → `"eyJ="`).
- `base64.urlsafe_b64decode(padded)` - uses URL-safe base64 (`-` instead of `+`, `_` instead of `/`), matching the JS `encodeTrackingId()` transform.
- `json.loads(decoded)` - parses the JSON string back to a Python dict.
- If anything fails (malformed ID, old encoding format), returns an empty dict. The caller handles missing keys gracefully with `.get()` defaults.

### _check_api_key() (line ~100)

```python
def _check_api_key(req):
    if not API_KEY:          # no key configured → open access (local dev)
        return True
    key = req.params.get("api_key", "")
    return key == API_KEY
```

If `TRACKING_API_KEY` is not set in Azure app settings, all endpoints are open (convenient for local dev). In production, set the key and pass `?api_key=your-key` on all analytics/sync endpoints.

### track_open() (line ~110)

```python
@app.route(route="api/o/{email_id}", methods=["GET"])
def track_open(req):
    email_id = req.route_params.get("email_id", "")
    _record_event("open", email_id, req)
    return func.HttpResponse(
        body=PIXEL,
        mimetype="image/gif",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
```

**Line-by-line:**
- `@app.route(route="api/o/{email_id}")` - the `{email_id}` path parameter is extracted by Azure and available via `req.route_params`. The `o` stands for "open" - kept short to minimize URL length in email HTML.
- `_record_event("open", email_id, req)` - fire-and-forget. We don't `await` or check the result. The pixel response must be fast.
- The cache headers are critical. `Cache-Control: no-cache, no-store, must-revalidate` tells Gmail's image proxy "do NOT cache this." Gmail prefetches all images through its proxy. Without this header, Gmail loads the pixel once when the email arrives, and subsequent actual opens by the user don't trigger new requests. Every email client that respects `Cache-Control` will re-fetch the pixel on each open.
- `Pragma: no-cache` - for older HTTP/1.0 clients.
- `Expires: 0` - for very old clients.

### track_click() (line ~125)

```python
@app.route(route="api/c/{link_id}", methods=["GET"])
def track_click(req):
    link_id = req.route_params.get("link_id", "")
    meta = _decode_email_id(link_id)
    target_url = meta.get("l", "https://speakhire.org")
    link_text = meta.get("t", "")
    email_id = meta.get("i", link_id)
    _record_event("click", email_id, req, link_url=target_url, link_text=link_text)
    # Backward compatibility: old links without 'l' field use ?r= query param
    if target_url == "https://speakhire.org":
        redirect_param = req.params.get("r", "")
        if redirect_param:
            target_url = unquote(redirect_param)
    return func.HttpResponse(status_code=302, headers={"Location": target_url})
```

**Line-by-line:**
- `meta = _decode_email_id(link_id)` - for click IDs, the metadata includes `l` (the real URL) and `t` (link text).
- `meta.get("l", "https://speakhire.org")` - the real URL to redirect to. Fallback to SpeakHire's homepage if missing.
- `email_id = meta.get("i", link_id)` - the `i` field cross-references the original tracking info (set to `email|campaign` by `encodeTrackingId()`). This allows the click event to be associated with the right recipient.
- The backward-compat block handles old tracking IDs that were created before the `l` field existed. Those old links use `?r=https%3A%2F%2Fspeakhire.org%2Fregister` as a query parameter instead.
- `302 Found` redirect. Uses 302 (temporary) rather than 301 (permanent) so browsers don't cache the redirect and skip tracking on subsequent clicks.

### sheet_sync() (line ~265)

The most performance-sensitive endpoint - called by Apps Script for every email in a campaign.

```python
@app.route(route="api/sheet/{campaign}", methods=["GET"])
def sheet_sync(req):
    if not _check_api_key(req):
        return func.HttpResponse("Unauthorized", status_code=401)

    campaign = req.route_params.get("campaign", "")
    days = int(req.params.get("days", "30"))
    emails_filter = req.params.get("emails", "")

    # Parse requested emails into a set
    requested = set()
    if emails_filter:
        requested = {e.strip().lower() for e in emails_filter.split(",") if e.strip()}

    # Query single partition - no cross-partition scan needed
    partition = f"campaign_{campaign}"
    items = _container.query_items(
        query="SELECT * FROM c WHERE c.partition_key = @pk",
        parameters=[{"name": "@pk", "value": partition}],
    )

    # Aggregate in memory: email → {opens, clicks, last_open, last_click}
    stats = {}
    for item in items:
        email = item.get("recipient_email", "").lower()
        if not email: continue
        if requested and email not in requested: continue
        # Days filter
        ts = item.get("timestamp", "")
        try:
            event_date = datetime.fromisoformat(ts)
            age = (datetime.now(timezone.utc) - event_date.replace(tzinfo=timezone.utc)).days
            if age > days: continue
        except Exception: continue

        if email not in stats:
            stats[email] = {"opens": 0, "clicks": 0, "last_open": None, "last_click": None}
        if item.get("event_type") == "open":
            stats[email]["opens"] += 1
            if not stats[email]["last_open"] or ts > stats[email]["last_open"]:
                stats[email]["last_open"] = ts
        elif item.get("event_type") == "click":
            stats[email]["clicks"] += 1
            if not stats[email]["last_click"] or ts > stats[email]["last_click"]:
                stats[email]["last_click"] = ts

    # Fill zeroes for requested emails that have no events
    for e in requested:
        if e not in stats:
            stats[e] = {"opens": 0, "clicks": 0, "last_open": None, "last_click": None}

    return func.HttpResponse(body=json.dumps({"campaign": campaign, "emails": stats}))
```

**Design decisions:**
- **Single partition query.** The query filters on `partition_key = campaign_{slug}`. Cosmos reads only one physical partition - fast and cheap (~3 RU for hundreds of events).
- **In-memory aggregation.** Rather than doing `GROUP BY` in Cosmos SQL (which is limited and expensive), we stream all events for the campaign and aggregate in Python. For a campaign with 500 events, this takes ~5ms.
- **Days filter in Python, not SQL.** Cosmos SQL doesn't have great date filtering. We fetch all events in the partition and filter by age in Python.
- **One API call per campaign, not per email.** Apps Script's `UrlFetchApp` has ~200ms latency per call. If you have 50 emails, 50 calls = 10 seconds. One batched call = 200ms.
- **Fill zeroes.** If Apps Script asks about `jane@x.com` but she has no events, we return `{"opens": 0, "clicks": 0}` rather than omitting her. This means the sheet always gets data for every row it asks about.

### dashboard() (line ~334)

Cross-partition query that builds the dashboard summary:

```python
@app.route(route="api/dashboard", methods=["GET"])
def dashboard(req):
    # ...auth, days filter...
    items = _container.query_items(
        query="SELECT * FROM c",
        enable_cross_partition_query=True,   # scans ALL campaign partitions
    )
    # Aggregate: campaigns[slug] → {total_opens, total_clicks, unique_emails(set), orgs(set), daily: {date: {opens, clicks}}}
    # Returns: {"totals": {...}, "campaigns": [...], "recent_activity": [...]}
```

**Design decisions:**
- **Cross-partition query** - more expensive (~10-50 RU) but acceptable because it runs on-demand (manual menu click), not per-open.
- **Unique counts via Python sets** - accumulate `unique_emails` and `orgs` as sets, then call `len()` at the end. Simpler and more reliable than Cosmos `COUNT(DISTINCT)`.
- **Daily breakdown** - the `daily` dict tracks opens/clicks per date, used by the dashboard for the daily trend table.

### analytics() (line ~160)

Campaign-level analytics with per-org breakdown. Similar to `dashboard()` but scoped to one campaign (single partition query when `{campaign}` is specified, cross-partition for "all").

### email_detail() (line ~424)

Returns all events for a single recipient: `GET /api/email/jane@x.com?api_key=KEY`. Useful for debugging: "Did jane's tracking pixel fire? How many times?"

## Code walkthrough: sheet_addons.js

This file is pasted INTO each campaign send script. It depends on variables from the parent:

| Variable | Source | Purpose |
|---|---|---|
| `SHEET_NAME` | Parent script config | Which tab to read/write |
| `COL_EMAIL` | Parent column mapping | Which column has email addresses |
| `CAMPAIGN_SLUG` | Tracking config section | e.g. `"speaking_my_name"` - becomes Cosmos partition key |

### encodeTrackingId() - the core logic

The JavaScript counterpart to `_decode_email_id()` in Python. They must stay in sync.

```javascript
function encodeTrackingId(email, name, orgName, campaign, linkUrl, linkText) {
  var payload = {
    e: email || "",    // → Python: meta.get("e")
    n: name || "",     // → Python: meta.get("n")
    o: orgName || "",  // → Python: meta.get("o")
    c: campaign || "", // → Python: meta.get("c")
  };
  if (linkUrl) {
    payload.l = linkUrl;                         // → Python: meta.get("l")
    payload.t = linkText || "";                  // → Python: meta.get("t")
    payload.i = (email || "") + "|" + (campaign || "");  // → Python: meta.get("i")
  }
  var json = JSON.stringify(payload);
  var encoded = Utilities.base64Encode(json, Utilities.Charset.UTF_8);
  return encoded.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
```

**Why single-letter field names:** Shorter keys = shorter encoded ID. `{"e":"jane@x.com",...}` encodes smaller than `{"email":"jane@x.com",...}`. This matters because the full URL is embedded in every email - shorter means less risk of Gmail's 102KB clipping threshold.

**Why URL-safe base64:** Standard base64 uses `+`, `/`, and `=`, which have special meanings in URLs (`+` = space, `/` = path separator, `=` = query param). The transform `.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")` makes it URL-safe.

### getTrackingPixel()

Builds the `<img>` tag embedded in every email:

```javascript
function getTrackingPixel(email, name, orgName, campaign) {
  var id = encodeTrackingId(email, name, orgName, campaign);
  return '<img src="' + TRACKING_BASE_URL + '/api/o/' + id + '"' +
    ' width="1" height="1" alt=""' +
    ' style="display:none !important;visibility:hidden !important;' +
    'opacity:0 !important;width:1px !important;height:1px !important;" />';
}
```

The `!important` flags override any email client CSS. The `alt=""` prevents broken-image icons if the pixel fails to load. Inline `style` is used instead of CSS classes because many email clients strip `<style>` blocks but keep inline styles.

### syncTracking()

Called from the campaign menu. Writes per-row open/click counts back to the sheet:

1. Finds or auto-creates tracking columns (Opens, Clicks, Last Open, Last Click) via `getOrCreateTrackingColumn()`
2. Collects all email addresses from the sheet into a comma-separated list
3. Calls `GET /api/sheet/{campaign}?emails=...&api_key=...` - one call for all rows
4. Iterates the response: for each email, writes `opens`, `clicks`, `last_open`, `last_click` to the matching row

**Why auto-create columns:** `getOrCreateTrackingColumn()` scans the header row. If "Opens" doesn't exist, it appends a new column with bold formatting. No manual column setup needed - the first sync creates everything.

## How tracking is embedded in every email

In each send script's `sendBatch()`, the plain-text body is converted to HTML and the pixel is appended:

```javascript
// Convert plain text to basic HTML
var htmlBody = body
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/\n/g, "<br>");

// Append invisible tracking pixel
htmlBody += getTrackingPixel(email, recipientName, orgName, CAMPAIGN_SLUG);

// Wrap in styled container
htmlBody = '<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;">' +
           htmlBody + '</div>';

// Send with both plain text (fallback) and HTML (with pixel)
GmailApp.sendEmail(email, subject, body, {
  htmlBody: htmlBody,
  name: DEFAULT_SENDER_NAME,
});
```

**Both plain text and HTML are sent.** Email clients that prefer plain text get the body without the pixel. Clients that render HTML (Gmail, Outlook, Apple Mail) get the version with the pixel. This is Gmail best practice - always provide both.

## Maintenance tasks

### How to add a new campaign's tracking

**1.** In the new campaign's `*_send.js`, add at the top:
```javascript
var TRACKING_BASE_URL = "https://YOUR_FUNCTION.azurewebsites.net";
var CAMPAIGN_SLUG     = "your_new_campaign";
```

**2.** Paste the tracking helper functions (`encodeTrackingId`, `getTrackingPixel`) from `sheet_addons.js` into the send script.

**3.** In `dashboard.js`, add the slug to the campaigns array:
```javascript
var campaigns = ["speaking_my_name", "summit", "soiree", "general", "your_new_campaign"];
```

The Azure backend needs no changes - it reads the campaign from the tracking ID payload, and Cosmos auto-creates the partition on first write.

### How to update the Cosmos DB schema

If you want to track additional fields (e.g., campaign version, device type):

**In `sheet_addons.js` `encodeTrackingId()`:**
```javascript
var payload = {
  e: email, n: name, o: orgName, c: campaign,
  v: "2",  // NEW: campaign version
};
```

**In `function_app.py` `_record_event()`:**
```python
doc = {
    ...
    "campaign_version": meta.get("v", "1"),  # NEW - defaults to "1" for old events
    ...
}
```

Existing events without the `v` field default to `"1"`. No migration needed - Cosmos is schemaless. New events get the new field, old events won't have it, queries handle both.

### How to rotate the API key

```bash
# 1. Generate new key
python -c "import secrets; print(secrets.token_urlsafe(32))"

# 2. Update Azure
az functionapp config appsettings set \
  --name speakhire-tracker \
  --resource-group speakhire-tracking \
  --settings TRACKING_API_KEY="new-key-here"

# 3. Update TRACKING_API_KEY in every JS script
```

Azure restarts the function on setting change - the old key stops working immediately.

### How to delete old events

Either:
- Set a TTL policy: Cosmos DB → Data Explorer → Scale & Settings → Time to Live → `31536000` (1 year). Cosmos auto-deletes expired docs.
- Or manually: `SELECT * FROM c WHERE c.timestamp < "2026-01-01"` then delete matching documents.

### How to monitor costs

Portal → Cost Management → Cost analysis → filter by resource group `speakhire-tracking`. Set a budget alert at $5/month. At your volume (hundreds to low thousands), the monthly cost should be under $1.

## Common issues

### "Azure unreachable" in Google Sheet

1. `TRACKING_SYNC_URL` must be exactly `https://speakhire-tracker.azurewebsites.net` (no trailing slash)
2. Test the URL in a browser: `https://speakhire-tracker.azurewebsites.net/api/dashboard?api_key=KEY`
3. If browser works but Apps Script doesn't, check API key encoding - special characters need `encodeURIComponent()`

### Tracking pixel not recording

1. Send a test to yourself, open it, wait 30 seconds
2. Check Azure logs: Portal → Function App → Logs → look for `[OPEN]` lines
3. If no log entries: inspect raw email source (Gmail → Show Original) and search for `azurewebsites.net/api/o/`. The pixel URL might be malformed.
4. If logs exist but no Cosmos records: check `COSMOS_CONNECTION_STRING` in app settings

### Gmail proxy inflates open counts

Gmail prefetches images via its proxy (`googleusercontent.com` User-Agent). The `Cache-Control: no-cache` header mitigates this, but some prefetching is unavoidable. Check the `user_agent` field in events to distinguish proxy loads from human opens.

### Cold start latency

First request after deploy or idle takes 2-5 seconds on the Consumption plan. The pixel still loads (the GIF is 43 bytes and returns instantly). Only the Cosmos write has latency, and it's fire-and-forget - the user never notices.

## Testing locally

```bash
npm i -g azure-functions-core-tools@4
cd email_tracking
# Edit local.settings.json with your Cosmos connection string
func start

# Test endpoints
curl "http://localhost:7071/api/o/eyJlIjoiamFuZUB4LmNvbSIsIm4iOiJKYW5lIiwibyI6IlFDIENvbW11bml0eSBIb3VzZSIsImMiOiJzcGVha2luZ19teV9uYW1lIn0"
curl "http://localhost:7071/api/dashboard"
curl "http://localhost:7071/api/sheet/speaking_my_name?emails=jane@x.com"
```

Full pipeline test:
1. Deploy to a staging Function App
2. Update `TRACKING_BASE_URL` in one campaign
3. `sendTest()` - 1 email to yourself
4. Open it, wait 30 seconds
5. `syncTracking()` - should see "Opens: 1"

## Deploying updates

```bash
cd email_tracking
pip install -r requirements.txt --target .python_packages/lib/site-packages
func azure functionapp publish speakhire-tracker --python
```

Only changed files are uploaded. Deployment takes ~30 seconds with no downtime.
