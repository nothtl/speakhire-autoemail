"""
Azure Function app for SpeakHire email tracking.

Endpoints:
  GET  /api/o/{email_id}        — open tracking pixel (1×1 GIF)
  GET  /api/c/{link_id}         — click redirect (302)
  GET  /api/analytics/{campaign} — campaign stats
  GET  /api/analytics            — all-campaign summary

Deploy:
  func azure functionapp publish speakhire-tracker --python
"""

import os
import uuid
import json
import logging
import base64
from datetime import datetime, timezone
from urllib.parse import unquote

import azure.functions as func
from azure.cosmos import CosmosClient, exceptions

# ── Cosmos DB setup ──────────────────────────────────────────────────────────
COSMOS_CONN = os.environ["COSMOS_CONNECTION_STRING"]
DATABASE = "tracking"
CONTAINER = "events"

_client = CosmosClient.from_connection_string(COSMOS_CONN)
_db = _client.get_database_client(DATABASE)
_container = _db.get_container_client(CONTAINER)

# ── Auth ─────────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("TRACKING_API_KEY", "")

# ── 1×1 transparent GIF (43 bytes) ───────────────────────────────────────────
PIXEL = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _record_event(event_type: str, email_id: str, req: func.HttpRequest,
                  link_url: str = None, link_text: str = None):
    """Write a tracking event to Cosmos DB. Never fails — logs and swallows errors."""
    try:
        # Decode the email_id to get embedded metadata
        # Format: base64(json({e:email, n:name, o:org, c:campaign, l:link_url}))
        meta = _decode_email_id(email_id)

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
        logging.info(f"[{event_type.upper()}] {doc['recipient_email']} | {doc['campaign']}")
    except Exception as e:
        logging.warning(f"Failed to record {event_type} event: {e}")


def _decode_email_id(email_id: str) -> dict:
    """Decode a base64-encoded email ID back to metadata dict."""
    try:
        import json
        # Add padding if needed
        padded = email_id + "=" * (4 - len(email_id) % 4) if len(email_id) % 4 else email_id
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return {}


def _check_api_key(req: func.HttpRequest) -> bool:
    """Validate the api_key query parameter. If API_KEY is set, it must match."""
    if not API_KEY:
        return True
    key = req.params.get("api_key", "")
    return key == API_KEY


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINT: Open tracking pixel
# ═══════════════════════════════════════════════════════════════════════════════

@app.route(route="api/o/{email_id}", methods=["GET"])
def track_open(req: func.HttpRequest) -> func.HttpResponse:
    """Serve 1×1 tracking pixel and record an open event.

    Embed in HTML emails as:
        <img src="https://track.speakhire.org/api/o/{encoded_email_id}"
             width="1" height="1" alt="" />
    """
    email_id = req.route_params.get("email_id", "")

    _record_event("open", email_id, req)

    return func.HttpResponse(
        body=PIXEL,
        mimetype="image/gif",
        status_code=200,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINT: Click redirect
# ═══════════════════════════════════════════════════════════════════════════════

@app.route(route="api/c/{link_id}", methods=["GET"])
def track_click(req: func.HttpRequest) -> func.HttpResponse:
    """Record a click event and 302 redirect to the original URL.

    Wrap links in emails as:
        <a href="https://track.speakhire.org/api/c/{encoded_link_id}">
    """
    link_id = req.route_params.get("link_id", "")

    # Decode the link_id to get original URL
    meta = _decode_email_id(link_id)
    target_url = meta.get("l", "https://speakhire.org")
    link_text = meta.get("t", "")
    email_id = meta.get("i", link_id)

    _record_event("click", email_id, req, link_url=target_url, link_text=link_text)

    # If the target URL wasn't in the metadata (old encoding), check query param
    if target_url == "https://speakhire.org":
        redirect_param = req.params.get("r", "")
        if redirect_param:
            target_url = unquote(redirect_param)

    return func.HttpResponse(
        status_code=302,
        headers={"Location": target_url},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINT: Campaign analytics
# ═══════════════════════════════════════════════════════════════════════════════

@app.route(route="api/analytics/{campaign}", methods=["GET"])
@app.route(route="api/analytics", methods=["GET"])
def analytics(req: func.HttpRequest) -> func.HttpResponse:
    """Return open/click stats for a campaign or across all campaigns.

    Query: ?api_key=xxx&days=7
    """
    if not _check_api_key(req):
        return func.HttpResponse("Unauthorized", status_code=401)

    campaign = req.route_params.get("campaign", "")
    days = int(req.params.get("days", "7"))

    partition = f"campaign_{campaign}" if campaign else None

    query = "SELECT * FROM c WHERE "
    params = []

    if partition:
        # Query specific campaign partition
        # Cosmos requires partition key for efficient queries
        items = _container.query_items(
            query="SELECT * FROM c WHERE c.partition_key = @pk",
            parameters=[{"name": "@pk", "value": partition}],
            enable_cross_partition_query=False,
        )
    else:
        items = _container.query_items(
            query="SELECT * FROM c",
            enable_cross_partition_query=True,
        )

    # Aggregate in memory
    opens_by_email = {}
    clicks_by_email = {}
    opens_by_org = {}
    clicks_by_org = {}

    for item in items:
        ts = item.get("timestamp", "")
        try:
            event_date = datetime.fromisoformat(ts)
        except Exception:
            continue

        # Days filter
        age = (datetime.now(timezone.utc) - event_date.replace(tzinfo=timezone.utc)).days
        if age > days:
            continue

        email = item.get("recipient_email", "unknown")
        org = item.get("org_name", "unknown")

        if item.get("event_type") == "open":
            opens_by_email[email] = opens_by_email.get(email, 0) + 1
            opens_by_org[org] = opens_by_org.get(org, 0) + 1
        elif item.get("event_type") == "click":
            clicks_by_email[email] = clicks_by_email.get(email, 0) + 1
            clicks_by_org[org] = clicks_by_org.get(org, 0) + 1

    # Unique counts
    unique_opens = len(opens_by_email)
    unique_clicks = len(clicks_by_email)
    total_opens = sum(opens_by_email.values())
    total_clicks = sum(clicks_by_email.values())

    # Per-org breakdown
    all_orgs = set(list(opens_by_org.keys()) + list(clicks_by_org.keys()))
    by_org = []
    for org in sorted(all_orgs):
        by_org.append({
            "org": org,
            "opens": opens_by_org.get(org, 0),
            "clicks": clicks_by_org.get(org, 0),
            "open_rate": None,  # need sent count to calculate
        })

    result = {
        "campaign": campaign or "all",
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "unique_opens": unique_opens,
            "unique_clicks": unique_clicks,
            "total_opens": total_opens,
            "total_clicks": total_clicks,
        },
        "by_org": by_org,
    }

    return func.HttpResponse(
        body=json.dumps(result, indent=2),
        mimetype="application/json",
        status_code=200,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINT: Sheet sync — returns tracking data keyed by email
# ═══════════════════════════════════════════════════════════════════════════════

@app.route(route="api/sheet/{campaign}", methods=["GET"])
def sheet_sync(req: func.HttpRequest) -> func.HttpResponse:
    """Return open/click counts per email for a campaign.
    Designed for Google Apps Script to call and write back to the sheet.

    Query: ?api_key=xxx&emails=a@b.com,c@d.com&days=30

    Returns:
      {
        "emails": {
          "jane@example.org": {"opens": 3, "clicks": 1, "last_open": "...", "last_click": "..."},
          ...
        }
      }
    """
    if not _check_api_key(req):
        return func.HttpResponse("Unauthorized", status_code=401)

    campaign = req.route_params.get("campaign", "")
    days = int(req.params.get("days", "30"))
    emails_filter = req.params.get("emails", "")

    # Parse requested emails into a set (case-insensitive)
    requested = set()
    if emails_filter:
        requested = {e.strip().lower() for e in emails_filter.split(",") if e.strip()}

    partition = f"campaign_{campaign}"

    items = _container.query_items(
        query="SELECT * FROM c WHERE c.partition_key = @pk",
        parameters=[{"name": "@pk", "value": partition}],
        enable_cross_partition_query=False,
    )

    # Aggregate: email → {opens, clicks, last_open, last_click}
    stats = {}

    for item in items:
        email = item.get("recipient_email", "").lower()
        if not email:
            continue

        # Filter to requested emails if specified
        if requested and email not in requested:
            continue

        ts = item.get("timestamp", "")
        try:
            event_date = datetime.fromisoformat(ts)
        except Exception:
            continue

        age = (datetime.now(timezone.utc) - event_date.replace(tzinfo=timezone.utc)).days
        if age > days:
            continue

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

    # If specific emails were requested, fill in zeroes for missing ones
    if requested:
        for e in requested:
            if e not in stats:
                stats[e] = {"opens": 0, "clicks": 0, "last_open": None, "last_click": None}

    return func.HttpResponse(
        body=json.dumps({
            "campaign": campaign,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "emails": stats,
        }, indent=2),
        mimetype="application/json",
        status_code=200,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINT: Dashboard — aggregate stats for Google Sheet dashboard tab
# ═══════════════════════════════════════════════════════════════════════════════

@app.route(route="api/dashboard", methods=["GET"])
def dashboard(req: func.HttpRequest) -> func.HttpResponse:
    """Return aggregate stats across all campaigns for the dashboard tab.

    Query: ?api_key=xxx&days=30
    """
    if not _check_api_key(req):
        return func.HttpResponse("Unauthorized", status_code=401)

    days = int(req.params.get("days", "30"))

    items = _container.query_items(
        query="SELECT * FROM c",
        enable_cross_partition_query=True,
    )

    # Per-campaign aggregates
    campaigns = {}  # campaign_slug → {opens, clicks, unique_emails, orgs, daily: {date: {opens, clicks}}}
    recent = []

    for item in items:
        ts = item.get("timestamp", "")
        try:
            event_date = datetime.fromisoformat(ts)
        except Exception:
            continue

        age = (datetime.now(timezone.utc) - event_date.replace(tzinfo=timezone.utc)).days
        if age > days:
            continue

        campaign = item.get("campaign", "unknown")
        email = item.get("recipient_email", "").lower()
        org = item.get("org_name", "unknown")
        event_type = item.get("event_type", "")
        date_key = ts[:10]  # "2026-06-10"

        if campaign not in campaigns:
            campaigns[campaign] = {
                "total_opens": 0,
                "total_clicks": 0,
                "unique_emails": set(),
                "orgs": set(),
                "daily": {},
            }

        c = campaigns[campaign]

        if date_key not in c["daily"]:
            c["daily"][date_key] = {"opens": 0, "clicks": 0}

        if event_type == "open":
            c["total_opens"] += 1
            c["daily"][date_key]["opens"] += 1
        elif event_type == "click":
            c["total_clicks"] += 1
            c["daily"][date_key]["clicks"] += 1

        if email:
            c["unique_emails"].add(email)
        if org != "unknown":
            c["orgs"].add(org)

        # Track recent activity (last 15 events)
        recent.append({
            "campaign": campaign,
            "event": event_type,
            "email": email,
            "org": org,
            "time": ts,
        })

    # Build clean output
    campaign_list = []
    grand_total_opens = 0
    grand_total_clicks = 0
    grand_unique = 0
    grand_orgs = 0

    for slug, c in sorted(campaigns.items()):
        unique = len(c["unique_emails"])
        org_count = len(c["orgs"])
        grand_total_opens += c["total_opens"]
        grand_total_clicks += c["total_clicks"]
        grand_unique += unique
        grand_orgs += org_count

        # Sort daily data for charts
        daily_sorted = dict(sorted(c["daily"].items()))

        campaign_list.append({
            "campaign": slug,
            "total_opens": c["total_opens"],
            "total_clicks": c["total_clicks"],
            "unique_recipients": unique,
            "orgs_reached": org_count,
            "daily": daily_sorted,
        })

    # Sort recent by time, most recent first
    recent.sort(key=lambda x: x["time"], reverse=True)
    recent = recent[:15]

    return func.HttpResponse(
        body=json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "days": days,
            "totals": {
                "opens": grand_total_opens,
                "clicks": grand_total_clicks,
                "unique_recipients": grand_unique,
                "orgs_reached": grand_orgs,
                "campaigns": len(campaign_list),
            },
            "campaigns": campaign_list,
            "recent_activity": recent,
        }, indent=2),
        mimetype="application/json",
        status_code=200,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINT: Email-level detail
# ═══════════════════════════════════════════════════════════════════════════════

@app.route(route="api/email/{email}", methods=["GET"])
def email_detail(req: func.HttpRequest) -> func.HttpResponse:
    """Return all events for a specific recipient email.

    Query: ?api_key=xxx&campaign=speaking_my_name
    """
    if not _check_api_key(req):
        return func.HttpResponse("Unauthorized", status_code=401)

    email = req.route_params.get("email", "").lower()
    campaign = req.params.get("campaign", "")

    # Query by partition if campaign specified
    if campaign:
        items = _container.query_items(
            query="SELECT * FROM c WHERE c.partition_key = @pk AND c.recipient_email = @email",
            parameters=[
                {"name": "@pk", "value": f"campaign_{campaign}"},
                {"name": "@email", "value": email},
            ],
            enable_cross_partition_query=False,
        )
    else:
        items = _container.query_items(
            query="SELECT * FROM c WHERE c.recipient_email = @email",
            parameters=[{"name": "@email", "value": email}],
            enable_cross_partition_query=True,
        )

    events = sorted(
        [dict(it) for it in items],
        key=lambda x: x.get("timestamp", ""),
        reverse=True,
    )

    return func.HttpResponse(
        body=json.dumps({"email": email, "events": events}, indent=2, default=str),
        mimetype="application/json",
        status_code=200,
    )

