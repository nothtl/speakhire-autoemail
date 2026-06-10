"""
generate.py — SpeakHire Email Draft Generator.

One command:  python generate.py

Reads the Google Sheet. For every row where STATUS=READY_FOR_RESEARCH,
reads the CAMPAIGN_TYPE dropdown (sponsor/partner/individual), researches the
org, generates a personalised email draft via AI, writes it back to the sheet,
and sets STATUS=DRAFTED. No sending. No flags. No local files.
"""

import json
import logging
import os
import re
import sys
from typing import Dict, List

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKER_DIR = os.environ.get(
    "OUTREACH_WORKER_DIR",
    os.path.join(SCRIPT_DIR, "speakhire-outreach-simple"),
)
if not os.path.isdir(WORKER_DIR):
    WORKER_DIR = os.path.join(SCRIPT_DIR, "speakhire-outreach-shared")

sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, WORKER_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, '_data'))

from dotenv import load_dotenv

# Load .env from the right directory
for env_dir in ["speakhire-outreach-simple", "speakhire-outreach-shared", "."]:
    env_path = os.path.join(SCRIPT_DIR, env_dir, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break
else:
    load_dotenv()

from campaign_prompts import get_prompt, get_sender, CAMPAIGN_TYPES
import outreach_worker as worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("generate")

# ── Config (from .env) ─────────────────────────────────────────────────────
SHEET_URL = os.getenv("GOOGLE_SHEET_URL", "")
CREDS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
if not SHEET_URL:
    sys.exit("GOOGLE_SHEET_URL not set in .env")
if not CREDS_PATH:
    sys.exit("GOOGLE_APPLICATION_CREDENTIALS not set in .env")

# ── Spreadsheet columns ────────────────────────────────────────────────────
ROW_COLUMNS = [
    "ORG_NAME", "ORG_WEBSITE", "RECIPIENT", "EMAIL",
    "CONTACT_FIRST_NAME", "CONTACT_LAST_NAME",
    "STATUS", "CAMPAIGN_TYPE", "NOTES",
    "PERSONALISED_OPENER", "EMAIL_SUBJECT", "EMAIL_DRAFT",
    "SENDER_NAME", "SENDER_TITLE", "SENDER_ORG",
    "OPT_OUT", "ERROR",
    "CONTACT_PAGE_URL", "ORG_TYPE", "SEGMENT", "PRIORITY",
    "RESEARCH_QUERY", "EVIDENCE_TITLE", "EVIDENCE_SUMMARY",
    "SOURCE_URL", "SOURCE_DATE", "RELEVANT_THEME", "EVIDENCE_CONFIDENCE",
    "CTA_TYPE", "CALL_DURATION",
    "LAST_UPDATED",
]

OUTPUT_COLUMNS = {
    "RESEARCH_QUERY", "EVIDENCE_TITLE", "EVIDENCE_SUMMARY", "SOURCE_URL",
    "SOURCE_DATE", "RELEVANT_THEME", "EVIDENCE_CONFIDENCE",
    "PERSONALISED_OPENER", "EMAIL_SUBJECT", "EMAIL_DRAFT",
    "CTA_TYPE", "CALL_DURATION", "STATUS",
    "ERROR",
}


# ═══════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS I/O
# ═══════════════════════════════════════════════════════════════════════════

def _sheet_id(url: str) -> str:
    m = re.search(r"/d/([a-zA-Z0-9\-_]+)", url)
    if not m:
        raise ValueError(f"Bad sheet URL: {url}")
    return m.group(1)


def _get_worksheet():
    import gspread
    gc = gspread.service_account(filename=CREDS_PATH)
    sh = gc.open_by_key(_sheet_id(SHEET_URL))
    try:
        return sh.worksheet("Outreach Tracker")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet("Outreach Tracker", rows=1000, cols=len(ROW_COLUMNS))
        ws.update("A1", [ROW_COLUMNS])
        log.info("Created 'Outreach Tracker' worksheet")
        return ws


def read_rows() -> List[Dict]:
    """Read all rows from the Google Sheet."""
    ws = _get_worksheet()
    data = ws.get_all_records(expected_headers=ROW_COLUMNS)
    rows = []
    for i, rd in enumerate(data):
        r = {}
        for c in ROW_COLUMNS:
            val = rd.get(c, "")
            if val is None or (isinstance(val, float) and str(val) == "nan"):
                val = ""
            r[c] = str(val).strip()
        # Derive RECIPIENT if empty
        if not r.get("RECIPIENT", ""):
            first = r.get("CONTACT_FIRST_NAME", "")
            last = r.get("CONTACT_LAST_NAME", "")
            if first and last:
                r["RECIPIENT"] = f"{first} {last}"
            elif first:
                r["RECIPIENT"] = first
        r["_row"] = i + 2  # row 1 = header
        rows.append(r)
    log.info(f"Read {len(rows)} rows from Google Sheets")
    return rows


def write_rows(rows: List[Dict], changed_indices: List[int]) -> None:
    """Write back only the changed rows."""
    if not changed_indices:
        return
    ws = _get_worksheet()
    for i in changed_indices:
        if i >= len(rows):
            continue
        r = rows[i]
        rn = r.get("_row", i + 2)
        row_data = [r.get(c, "") for c in ROW_COLUMNS]
        ws.update(f"A{rn}", [row_data])
    log.info(f"Updated {len(changed_indices)} rows in Google Sheets")


# ═══════════════════════════════════════════════════════════════════════════
# DRAFT GENERATION
# ═══════════════════════════════════════════════════════════════════════════

def generate_for_row(row: Dict) -> Dict:
    """Generate a draft for one row. Returns dict of columns to update."""
    rn = row["_row"]
    campaign = row.get("CAMPAIGN_TYPE", "").strip().lower()

    if campaign not in CAMPAIGN_TYPES:
        return {"STATUS": "ERROR", "ERROR": f"Invalid CAMPAIGN_TYPE: '{campaign}'. Must be: {', '.join(CAMPAIGN_TYPES)}"}

    org_name = row.get("ORG_NAME", "")
    if not org_name:
        return {"STATUS": "ERROR", "ERROR": "No ORG_NAME"}

    org_website = row.get("ORG_WEBSITE", "")
    recipient = row.get("RECIPIENT", "") or "Partnerships Team"
    email = row.get("EMAIL", "")
    org_type = row.get("ORG_TYPE", "")
    segment = row.get("SEGMENT", "")
    notes = row.get("NOTES", "")
    has_email = bool(email and worker.validate_email(email))

    # Patch worker with campaign-specific prompt + sender
    worker.SYSTEM_PROMPT = get_prompt(campaign)
    sender = get_sender(campaign)
    worker.DEFAULT_SENDER_NAME = sender["name"]
    worker.DEFAULT_SENDER_ORG = sender["org"]

    _orig_get_sender_title = worker.get_sender_title
    worker.get_sender_title = lambda r: worker.clean(r.get("SENDER_TITLE")) or sender["title"]

    sender_name = row.get("SENDER_NAME", "").strip() or sender["name"]
    sender_org = row.get("SENDER_ORG", "").strip() or sender["org"]
    sender_title = row.get("SENDER_TITLE", "").strip() or sender["title"]

    log.info(f"Row {rn}: [{campaign}] {org_name}")

    try:
        if campaign == "individual" and not org_website:
            search_query = "(individual invite — no web research)"
            search_results = [{"title": "Profile", "snippet": f"Profile notes: {notes}\nSegment: {segment}", "url": ""}]
            website_text = ""
        else:
            search_query, search_results, website_text = worker.research(org_name, org_website)

        draft = worker.generate_draft(
            org_name, recipient, org_type, segment,
            search_results, website_text, has_email,
            sender_name, sender_org, sender_title,
        )
    except Exception as e:
        log.error(f"Row {rn}: {e}")
        draft = worker._fallback_draft(org_name, recipient, has_email,
                                       sender_name, sender_org, sender_title, str(e))
        search_query = ""

    worker.get_sender_title = _orig_get_sender_title

    # Map draft fields (lowercase from LLM) to OUTPUT_COLUMNS (uppercase for sheet)
    field_map = {
        "RESEARCH_QUERY": "research_query",
        "EVIDENCE_TITLE": "evidence_title",
        "EVIDENCE_SUMMARY": "evidence_summary",
        "SOURCE_URL": "source_url",
        "SOURCE_DATE": "source_date",
        "RELEVANT_THEME": "relevant_theme",
        "EVIDENCE_CONFIDENCE": "evidence_confidence",
        "PERSONALISED_OPENER": "personalised_opener",
        "EMAIL_SUBJECT": "email_subject",
        "EMAIL_DRAFT": "email_draft",
        "CTA_TYPE": "cta_type",
        "CALL_DURATION": "call_duration",
        "ERROR": "error",
    }
    out = {}
    for col, key in field_map.items():
        out[col] = draft.get(key, "")
    out["RESEARCH_QUERY"] = search_query or ""
    out["STATUS"] = "ERROR" if draft.get("error") else "DRAFTED"
    out["LAST_UPDATED"] = worker.ts_now()

    conf = draft.get("evidence_confidence", "N/A")
    log.info(f"Row {rn}: {out['STATUS']} (confidence: {conf})")
    return out


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print(f"Sheet: {SHEET_URL}")
    print(f"LLM:   {worker.LLM_PROVIDER} / {worker.MODEL_NAME}")
    print(f"Search:{worker.SEARCH_PROVIDER}")
    print()

    rows = read_rows()
    processed, errors, changed = [], [], []

    for i, row in enumerate(rows):
        status = row.get("STATUS", "").upper()

        # Only process READY_FOR_RESEARCH rows
        if status != "READY_FOR_RESEARCH":
            continue

        # Skip opt-outs
        if row.get("OPT_OUT", "").upper() in ("TRUE", "YES", "1"):
            continue

        # Validate CAMPAIGN_TYPE
        campaign = row.get("CAMPAIGN_TYPE", "").strip().lower()
        if campaign not in CAMPAIGN_TYPES:
            row["STATUS"] = "ERROR"
            row["ERROR"] = f"Missing CAMPAIGN_TYPE — set to: {', '.join(CAMPAIGN_TYPES)}"
            errors.append(f"Row {row['_row']}: {row.get('ORG_NAME', '')} — {row['ERROR']}")
            changed.append(i)
            continue

        output = generate_for_row(row)

        for col, val in output.items():
            if col in ROW_COLUMNS:
                row[col] = val

        changed.append(i)

        if output["STATUS"] == "ERROR":
            errors.append(f"Row {row['_row']}: {row.get('ORG_NAME', '')} — {output.get('ERROR', '')}")
        else:
            processed.append(f"Row {row['_row']}: {row['ORG_NAME']} [{campaign}]")

    # Write back
    write_rows(rows, changed)

    # Summary
    print(f"\n{'='*50}")
    print(f"  Generated: {len(processed)} drafts")
    print(f"  Errors:    {len(errors)}")
    print(f"{'='*50}")

    for p in processed:
        print(f"  OK  {p}")
    for e in errors:
        print(f"  ERR {e}")


if __name__ == "__main__":
    main()
