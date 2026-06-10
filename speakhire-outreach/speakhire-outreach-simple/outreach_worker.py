"""
outreach_worker.py -SpeakHire Outreach Automation

One-file backend: loads config, researches organisations via web search +
website scraping, generates personalised email drafts via LLM (DeepSeek/OpenAI),
writes results into Google Sheets or local .xlsx, and sends approved emails.

Usage:
  python outreach_worker.py               # local mode: process local .xlsx
  python outreach_worker.py --send         # send approved (local)
  python outreach_worker.py --approve      # auto-approve safe drafts (local)
  python outreach_worker.py --row 5        # process single row (local)
  uvicorn outreach_worker:app --reload     # FastAPI server for Apps Script
"""

# ============================================================================
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  EMAIL DRAFT PROMPT -Edit this to change what the AI writes            ║
# ║  This tells the AI how to structure the email, tone, length, etc.       ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
# ============================================================================
SYSTEM_PROMPT = """You are an outreach research and email drafting assistant for SpeakHire, a NYC-based nonprofit supporting underrepresented, primarily immigrant youth and communities in achieving economic mobility.

SpeakHire runs #SpeakingMyName -a campaign encouraging participants to record a short video sharing their name, pronunciation, and story. The campaign promotes belonging, respect, identity inclusion, and confidence for people whose names are often mispronounced.

Your job: analyse provided search results and website text about an organisation, then generate a personalised sponsorship/outreach email draft.

CRITICAL RULES:
1. NEVER invent organisation names, emails, people, events, awards, programmes, or contact details.
2. If unsure about any fact, leave that field blank.
3. Only use information clearly visible in the provided search results and website text.
4. HIGH confidence = specific, real event/program/initiative with a source URL that appears in the search results.
5. MEDIUM confidence = mission alignment is clear but specific evidence is less concrete.
6. LOW confidence = weak or no evidence; use ONLY a safe generic mission-alignment opener. Do not mention any specific program.
7. Tone: warm, concise, professional, nonprofit-friendly, human.
8. Keep the full email under 220 words.
9. Ask for a short 15-20 minute call to discuss partnership -do NOT demand sponsorship.
10. The first paragraph must mention the organisation's actual work ONLY if supported by real evidence from the search results.
11. NEVER include send instructions or auto-send language.
12. Use the sender name and organisation provided in the prompt exactly. Do NOT invent another sender name. Do NOT use [Your Name] or Your Name.
13. The intro line must be exactly: "I'm {sender_name} with {sender_org}, a NYC-based nonprofit..." as provided in the prompt.
14. The email sign-off must use exactly the signature block provided in the prompt.
15. NEVER use em dashes (—) or long dashes anywhere in the email. Use regular dashes (-) or commas instead.

Return this exact JSON structure (no markdown, no extra text):
{
  "evidence_title": "string -real event/program/initiative name, or empty",
  "evidence_summary": "string -short factual summary, or empty",
  "source_url": "string -source URL from search results, or empty",
  "source_date": "string -date if known, or empty",
  "relevant_theme": "string -why they connect to SpeakHire's mission",
  "evidence_confidence": "HIGH" or "MEDIUM" or "LOW",
  "personalised_opener": "string -customised first paragraph of email",
  "email_subject": "string",
  "email_draft": "string -full email body",
  "review_status": "NEEDS_REVIEW" or "NEEDS_CONTACT_INFO",
  "error": "string -empty if ok"
}
"""

# ═══ Default sender -change these to set who the email comes from ═══
DEFAULT_SENDER_NAME = "Hana"
DEFAULT_SENDER_ORG = "SpeakHire"

import json, os, re, sys, logging, argparse, hashlib
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple, Literal
from urllib.parse import urlparse

import pandas as pd
import requests
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("outreach_worker")

# ============================================================================
# CONFIG
# ============================================================================
def _env(k, d=""): return os.getenv(k, d)
def _env_bool(k, d=False): v = os.getenv(k, "").lower(); return v in ("true", "1", "yes") if v else d

LLM_PROVIDER      = _env("LLM_PROVIDER", "deepseek")
OPENAI_API_KEY    = _env("OPENAI_API_KEY")
DEEPSEEK_API_KEY  = _env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
MODEL_NAME        = _env("MODEL_NAME", "deepseek-chat")

SEARCH_PROVIDER   = _env("SEARCH_PROVIDER", "none")
SERPER_API_KEY    = _env("SERPER_API_KEY")
TAVILY_API_KEY    = _env("TAVILY_API_KEY")
SERPAPI_API_KEY   = _env("SERPAPI_API_KEY")

MODE              = _env("MODE", "local")
LOCAL_XLSX_PATH   = _env("LOCAL_XLSX_PATH", "sample_leads.xlsx")

GOOGLE_SHEET_URL  = _env("GOOGLE_SHEET_URL")
GOOGLE_APPLICATION_CREDENTIALS = _env("GOOGLE_APPLICATION_CREDENTIALS")
ALLOW_SHEET_WIPE  = _env_bool("ALLOW_SHEET_WIPE", False)

SENDGRID_API_KEY  = _env("SENDGRID_API_KEY")
FROM_EMAIL        = _env("FROM_EMAIL")
FROM_NAME         = _env("FROM_NAME", "Hana from SpeakHire")
DRY_RUN           = _env_bool("DRY_RUN", True)

# Gmail SMTP (free alternative to SendGrid)
USE_GMAIL         = _env_bool("USE_GMAIL", False)
GMAIL_USER        = _env("GMAIL_USER")
GMAIL_APP_PASSWORD = _env("GMAIL_APP_PASSWORD")

SHEETS_WEBHOOK_API_KEY = _env("SHEETS_WEBHOOK_API_KEY")
BACKEND_HOST      = _env("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT      = int(_env("BACKEND_PORT", "8000"))

# derive LLM credentials
if LLM_PROVIDER == "deepseek":
    LLM_API_KEY = DEEPSEEK_API_KEY
    LLM_BASE_URL = DEEPSEEK_BASE_URL
else:
    LLM_API_KEY = OPENAI_API_KEY
    LLM_BASE_URL = None

# ============================================================================
# CONSTANTS (for Google Sheets tracker + local xlsx)
# ============================================================================
ROW_COLUMNS = [
    # Primary (human-facing)
    "ORG_NAME","ORG_WEBSITE","RECIPIENT","EMAIL",
    "STATUS","NOTES",
    "PERSONALISED_OPENER","EMAIL_SUBJECT","EMAIL_DRAFT",
    "HUMAN_EDITED_DRAFT",
    "SENDER_NAME","OPT_OUT",
    "SENT_AT","ERROR",
    # System (auto-filled)
    "CONTACT_FIRST_NAME","CONTACT_LAST_NAME","CONTACT_PAGE_URL",
    "ORG_TYPE","SEGMENT","TAGS","PRIORITY",
    "RESEARCH_QUERY","EVIDENCE_TITLE","EVIDENCE_SUMMARY",
    "SOURCE_URL","SOURCE_DATE","RELEVANT_THEME","EVIDENCE_CONFIDENCE",
    "CTA_TYPE","CALL_DURATION",
    "SENDER_TITLE","SENDER_ORG","SEND_FROM",
    "EMAIL_PROVIDER_STATUS","FOLLOW_UP_DATE","FOLLOW_UP_STATUS",
    "LAST_UPDATED",
]

# Map old ROW_COLUMNS names for backward compat in existing code
_RC_MAP = {"FIRST_NAME":"CONTACT_FIRST_NAME","LAST_NAME":"CONTACT_LAST_NAME"}

TRACKER_COLUMNS = list(ROW_COLUMNS)  # same, for the Outreach Tracker tab

DROP_DOWN_VALUES = {
    "STATUS": ["READY_FOR_RESEARCH","DRAFTED","SENDING","SENT","SKIP","ERROR"],
    "REVIEW_STATUS": ["NEEDS_REVIEW","APPROVED","AUTO_APPROVED","REJECTED","NEEDS_CONTACT_INFO","EDITED"],
    "ORG_TYPE": ["nonprofit","school","university","company","association","foundation","government","community_group","unknown"],
    "SEGMENT": ["immigrant community","youth empowerment","education","DEI","workforce development","cultural identity","corporate CSR","community engagement","language access"],
    "PRIORITY": ["HIGH","MEDIUM","LOW"],
    "EVIDENCE_CONFIDENCE": ["HIGH","MEDIUM","LOW"],
    "FOLLOW_UP_STATUS": ["PENDING","SENT","NOT_NEEDED"],
    "OPT_OUT": ["TRUE","FALSE"],
}

OUTPUT_COLUMNS = {
    "RESEARCH_QUERY","EVIDENCE_TITLE","EVIDENCE_SUMMARY","SOURCE_URL",
    "SOURCE_DATE","RELEVANT_THEME","EVIDENCE_CONFIDENCE",
    "PERSONALISED_OPENER","EMAIL_SUBJECT","EMAIL_DRAFT",
    "CTA_TYPE","CALL_DURATION","STATUS",
    "ERROR","NOTES","SENT_AT","EMAIL_PROVIDER_STATUS",
}

# ============================================================================
# SCHEMAS
# ============================================================================
class OutreachDraft(BaseModel):
    evidence_title: str = ""
    evidence_summary: str = ""
    source_url: str = ""
    source_date: str = ""
    relevant_theme: str = ""
    evidence_confidence: Literal["HIGH", "MEDIUM", "LOW"] = "LOW"
    personalised_opener: str = ""
    email_subject: str = ""
    email_draft: str = ""
    review_status: Literal["NEEDS_REVIEW", "NEEDS_CONTACT_INFO"] = "NEEDS_REVIEW"
    error: str = ""

# ============================================================================
# UTILITIES
# ============================================================================
def clean(val: Any) -> str:
    if val is None: return ""
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none", "null", "nat") else s

def validate_email(email: str) -> bool:
    if not email: return False
    return bool(re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email))

def ts_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def is_opt_out(row: dict) -> bool:
    return clean(row.get("OPT_OUT", "")).upper() in ("TRUE", "YES", "1")

# ============================================================================
# SENDER HELPERS -dynamic sender name/title/org from spreadsheet
# ============================================================================
def get_sender_name(row: dict) -> str:
    return clean(row.get("SENDER_NAME")) or DEFAULT_SENDER_NAME

def get_sender_org(row: dict) -> str:
    return clean(row.get("SENDER_ORG")) or DEFAULT_SENDER_ORG

def get_sender_title(row: dict) -> str:
    return clean(row.get("SENDER_TITLE"))

def build_signature(row: dict) -> str:
    name = get_sender_name(row)
    title = get_sender_title(row)
    org = get_sender_org(row)
    lines = ["Best,", name]
    if title:
        lines.append(title)
    lines.append(org)
    return "\n".join(lines)

def build_sender_intro(row: dict) -> str:
    name = get_sender_name(row)
    org = get_sender_org(row)
    return f"I'm {name} with {org}, a NYC-based nonprofit supporting underrepresented, primarily immigrant youth and communities in achieving economic mobility."

def _sheet_id_from_url(url: str) -> Optional[str]:
    m = re.search(r"/d/([a-zA-Z0-9\-_]+)", url)
    return m.group(1) if m else None

def domain_from_url(u: str) -> str:
    try:
        p = urlparse(u if "://" in u else f"https://{u}")
        return (p.netloc or p.path).replace("www.", "")
    except Exception:
        return ""

# ============================================================================
# DATA BACKEND -local xlsx or Google Sheets
# ============================================================================
def _read_rows(sheet_url: str = "", mode: str = None) -> List[Dict]:
    m = mode or MODE
    if m == "google":
        return _gsheet_read(sheet_url)
    else:
        return _local_read()

def _write_rows(rows: List[Dict], sheet_url: str = "", mode: str = None, changed_indices: List[int] = None) -> None:
    m = mode or MODE
    if m == "google":
        _gsheet_update_rows(rows, sheet_url, changed_indices)
    else:
        _local_write(rows)

def _local_read() -> List[Dict]:
    path = LOCAL_XLSX_PATH
    if not os.path.exists(path):
        log.warning(f"Local file not found: {path}. Creating blank.")
        pd.DataFrame(columns=ROW_COLUMNS).to_excel(path, index=False)
        return []
    df = pd.read_excel(path)
    for c in ROW_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    rows = []
    for idx, row in df.iterrows():
        rd = {c: clean(row.get(c, "")) for c in ROW_COLUMNS}
        # Derive RECIPIENT from CONTACT_FIRST_NAME + CONTACT_LAST_NAME if empty
        if not rd.get("RECIPIENT",""):
            first = rd.get("CONTACT_FIRST_NAME","")
            last = rd.get("CONTACT_LAST_NAME","")
            if first and last: rd["RECIPIENT"] = f"{first} {last}"
            elif first: rd["RECIPIENT"] = first
            else: rd["RECIPIENT"] = "Partnerships Team"
        rd["_row"] = idx + 2
        rows.append(rd)
    log.info(f"Read {len(rows)} rows from {path}")
    return rows

def _local_write(rows: List[Dict]) -> None:
    path = LOCAL_XLSX_PATH
    data = [{c: r.get(c, "") for c in ROW_COLUMNS} for r in rows]
    pd.DataFrame(data, columns=ROW_COLUMNS).to_excel(path, index=False)
    log.info(f"Wrote {len(rows)} rows to {path}")

def _gsheet_read(sheet_url: str = "") -> List[Dict]:
    import gspread
    url = sheet_url or GOOGLE_SHEET_URL
    if not GOOGLE_APPLICATION_CREDENTIALS:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS not set")
    gc = gspread.service_account(filename=GOOGLE_APPLICATION_CREDENTIALS)
    sid = _sheet_id_from_url(url)
    if not sid: raise ValueError(f"Bad sheet URL: {url}")
    ws = gc.open_by_key(sid).worksheet('Outreach Tracker')
    all_vals = ws.get_all_values()
    if not all_vals: return []

    # Normalise headers -ensure all ROW_COLUMNS exist; add missing ones
    raw_headers = all_vals[0]
    header_map = {}  # col_index -> ROW_COLUMNS key
    for i, h in enumerate(raw_headers):
        key = _norm_hdr(h)
        header_map[i] = key
    # add missing columns to sheet
    existing_keys = set(header_map.values())
    for col in ROW_COLUMNS:
        if col.upper() not in {k.upper() for k in existing_keys}:
            new_col = len(raw_headers) + 1
            ws.update_cell(1, new_col, col)
            raw_headers.append(col)
            header_map[new_col - 1] = col
    # parse rows
    rows = []
    for ri, row in enumerate(all_vals[1:], start=2):
        rd = {c: "" for c in ROW_COLUMNS}
        for i, h in enumerate(raw_headers):
            key = header_map.get(i, _norm_hdr(h))
            if key in rd:
                val = row[i] if i < len(row) else ""
                rd[key] = clean(val)
        # Derive RECIPIENT from CONTACT_FIRST_NAME + CONTACT_LAST_NAME if empty
        if not rd.get("RECIPIENT",""):
            first = rd.get("CONTACT_FIRST_NAME","")
            last = rd.get("CONTACT_LAST_NAME","")
            if first and last: rd["RECIPIENT"] = f"{first} {last}"
            elif first: rd["RECIPIENT"] = first
            else: rd["RECIPIENT"] = "Partnerships Team"
        rd["_row"] = ri
        rows.append(rd)
    return rows

def _gsheet_update_rows(rows: List[Dict], sheet_url: str = "", changed_indices: List[int] = None) -> None:
    """
    Update only changed rows/columns in Google Sheets.
    Never clears the sheet unless ALLOW_SHEET_WIPE=True and no prior data exists.
    """
    import gspread
    url = sheet_url or GOOGLE_SHEET_URL
    if not GOOGLE_APPLICATION_CREDENTIALS:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS not set")
    gc = gspread.service_account(filename=GOOGLE_APPLICATION_CREDENTIALS)
    sid = _sheet_id_from_url(url)
    if not sid: raise ValueError(f"Bad sheet URL: {url}")
    ws = gc.open_by_key(sid).worksheet('Outreach Tracker')

    # If sheet is totally empty and ALLOW_SHEET_WIPE, initialise it
    all_vals = ws.get_all_values()
    if not all_vals or not all_vals[0]:
        if ALLOW_SHEET_WIPE:
            headers = ROW_COLUMNS
            data = [headers] + [[str(r.get(c, "")) for c in headers] for r in rows]
            ws.update("A1", data, value_input_option="RAW")
            log.info(f"Initialised sheet with {len(rows)} rows")
            return
        else:
            log.warning("Sheet is empty but ALLOW_SHEET_WIPE=false -writing headers only")
            ws.update("A1", [ROW_COLUMNS], value_input_option="RAW")
            return

    raw_headers = ws.row_values(1)
    # build column index map
    col_map = {}  # ROW_COLUMNS -> 1-based col index
    for i, h in enumerate(raw_headers):
        key = _norm_hdr(h)
        col_map[key.upper()] = i + 1
    # ensure all OUTPUT_COLUMNS exist as headers
    for col in OUTPUT_COLUMNS:
        if col.upper() not in col_map:
            new_i = len(raw_headers) + 1
            ws.update_cell(1, new_i, col)
            col_map[col.upper()] = new_i
            raw_headers.append(col)

    # build index of existing rows by _row
    existing_rows = {ri: True for ri in range(2, len(all_vals) + 1)}

    # prepare batch updates for only OUTPUT_COLUMNS
    cells_to_update = []
    for row in rows:
        rn = row["_row"]
        # if row doesn't exist yet, append
        if rn not in existing_rows:
            rn = len(all_vals) + 1
            existing_rows[rn] = True
            row["_row"] = rn
        for col in OUTPUT_COLUMNS:
            val = str(row.get(col, ""))
            ci = col_map.get(col.upper())
            if ci:
                cells_to_update.append({"range": f"{_col_letter(ci)}{rn}", "values": [[val]]})

    # write in batches (gspread update_cells or batch_update)
    if cells_to_update:
        # use batch update for efficiency
        ws.batch_update(cells_to_update, value_input_option="RAW")
        log.info(f"Updated {len(cells_to_update)} cells across {len(rows)} rows")

def _norm_hdr(h: str) -> str:
    """Normalise a raw header string to a ROW_COLUMNS key."""
    h = clean(h).upper().replace(" ", "_").replace("-", "_").replace("/", "_").replace(".", "_")
    h = re.sub(r"_+", "_", h).strip(" _")
    mapping = {
        "ORGANIZATION":"ORG_NAME","ORGANIZATION_NAME":"ORG_NAME","ORG":"ORG_NAME",
        "ASSOCIATION_NAME":"ORG_NAME","COMPANY":"ORG_NAME","COLLEGE":"ORG_NAME",
        "SECONDARY_SCHOOLS":"ORG_NAME","SCHOOL":"ORG_NAME",
        "FULL_NAME":"RECIPIENT","CONTACT_PERSON":"RECIPIENT","NAME":"RECIPIENT",
        "EMAIL_OTHER_CONTACT_INFO":"EMAIL",
        "REACHED_OUT":"NOTES","RESPONSE":"NOTES","DATE":"SENT_AT",
        "TYPE":"ORG_TYPE","TYPE_OF_ASSOCIATION":"ORG_TYPE",
        "CLUB_ASSOCIATIONS":"SEGMENT","NOTES_ISSUES":"NOTES",
    }
    if h in mapping: return mapping[h]
    hf = h.replace("_", "")
    for c in ROW_COLUMNS:
        if c.replace("_", "") == hf: return c
    return h

def _col_letter(n: int) -> str:
    """Convert 1-based column number to letter(s)."""
    result = ""
    while n > 0:
        n -= 1
        result = chr(n % 26 + 65) + result
        n //= 26
    return result

# ============================================================================
# GOOGLE SHEET STRUCTURE -multi-tab setup with dropdowns & formatting
# ============================================================================
def _get_gsheet(sheet_url: str = None):
    import gspread
    if not GOOGLE_APPLICATION_CREDENTIALS:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS not set")
    gc = gspread.service_account(filename=GOOGLE_APPLICATION_CREDENTIALS)
    url = sheet_url or GOOGLE_SHEET_URL
    sid = _sheet_id_from_url(url)
    if not sid: raise ValueError(f"Bad sheet URL: {url}")
    return gc.open_by_key(sid)

def _get_or_create_tab(sh, name: str, cols: int = 1, rows: int = 100):
    """Get a tab by name, creating it if it doesn't exist."""
    try:
        return sh.worksheet(name)
    except:
        return sh.add_worksheet(title=name, rows=str(rows), cols=str(cols))

def initialise_google_sheet(sheet_url: str = "") -> Dict:
    """Create the full multi-tab SpeakHire Outreach sheet structure."""
    sh = _get_gsheet(sheet_url)
    results = {}
    results["tracker"] = _create_tracker_tab(sh)
    results["settings"] = _create_settings_tab(sh)
    results["tag_options"] = _create_tag_options_tab(sh)
    results["migration_log"] = _create_migration_log_tab(sh)
    log.info(f"Sheet initialised: {json.dumps(results)}")
    return {"status":"success","tabs":results}

def _create_tracker_tab(sh) -> str:
    ws = _get_or_create_tab(sh, "Outreach Tracker", cols=len(TRACKER_COLUMNS))
    existing = ws.row_values(1)
    if not existing or len(existing) < 2:
        ws.update("A1", [TRACKER_COLUMNS], value_input_option="RAW")
        _apply_dropdowns_and_formatting(ws)
        return "created"
    # ensure all columns present
    for i, col in enumerate(TRACKER_COLUMNS):
        if i >= len(existing) or col.upper() != (existing[i] or "").strip().upper():
            if col.upper() not in {h.strip().upper() for h in existing if h}:
                ws.update_cell(1, len(existing)+1, col)
                existing.append(col)
    _apply_dropdowns_and_formatting(ws)
    return "updated"

def _apply_dropdowns_and_formatting(ws) -> None:
    import gspread
    # Freeze header row + bold + filter
    ws.freeze(rows=1)
    header_range = f"A1:{_col_letter(len(TRACKER_COLUMNS))}1"
    ws.format(header_range, {"textFormat":{"bold":True}})
    try:
        ws.set_basic_filter()
    except Exception: pass
    # Apply data validation dropdowns
    col_indices = {c.upper(): i+1 for i, c in enumerate(TRACKER_COLUMNS)}
    for field, values in DROP_DOWN_VALUES.items():
        ci = col_indices.get(field.upper())
        if not ci: continue
        col = _col_letter(ci)
        try:
            ws.set_data_validation_for_range(
                f"{col}2:{col}1000",
                {"condition":{"type":"ONE_OF_LIST","values":[{"userEnteredValue":v} for v in values]},
                 "strict":True,"showCustomUi":True}
            )
        except Exception as e:
            log.debug(f"Data validation for {field}: {e}")
    # Wrap text columns
    wrap_cols = ["EMAIL_DRAFT","HUMAN_EDITED_DRAFT","EVIDENCE_SUMMARY","NOTES","ERROR","PERSONALISED_OPENER","EVIDENCE_TITLE"]
    for wc in wrap_cols:
        ci = col_indices.get(wc.upper())
        if ci:
            ws.format(f"{_col_letter(ci)}2:{_col_letter(ci)}1000", {"wrapStrategy":"WRAP"})
    # Conditional formatting
    try:
        _apply_cond_format(ws, col_indices)
    except Exception: pass
    log.info("Dropdowns + formatting applied")

def _apply_cond_format(ws, col_indices) -> None:
    import gspread
    rules = [
        ("STATUS","SENT",0.8,1,0.8),
        ("STATUS","ERROR",1,0.8,0.8),
        ("REVIEW_STATUS","APPROVED",0.8,1,0.8),
        ("REVIEW_STATUS","AUTO_APPROVED",0.8,1,0.8),
        ("REVIEW_STATUS","NEEDS_REVIEW",1,1,0.7),
        ("REVIEW_STATUS","NEEDS_CONTACT_INFO",1,0.85,0.7),
        ("EVIDENCE_CONFIDENCE","HIGH",0.8,1,0.8),
        ("EVIDENCE_CONFIDENCE","LOW",1,0.8,0.8),
    ]
    for field, value, r, g, b in rules:
        ci = col_indices.get(field.upper())
        if not ci: continue
        col = _col_letter(ci)
        try:
            ws.add_conditional_formatting_for_range(
                f"{col}2:{col}1000",
                {"booleanRule":{"condition":{"type":"TEXT_EQ","values":[{"userEnteredValue":value}]},
                 "format":{"backgroundColor":{"red":r,"green":g,"blue":b}}}}
            )
        except Exception: pass

def _create_settings_tab(sh) -> str:
    ws = _get_or_create_tab(sh, "Settings", cols=2, rows=10)
    existing = ws.row_values(1)
    if not existing or existing[0] != "SETTING":
        ws.update("A1:B1", [["SETTING","VALUE"]], value_input_option="RAW")
    defaults = [
        ["DEFAULT_SENDER_NAME",DEFAULT_SENDER_NAME],
        ["DEFAULT_SENDER_TITLE",""],
        ["DEFAULT_SENDER_ORG",DEFAULT_SENDER_ORG],
        ["DEFAULT_CALL_DURATION","15-20 minutes"],
        ["DRY_RUN","TRUE" if DRY_RUN else "FALSE"],
    ]
    # upsert settings
    all_vals = ws.get_all_values()
    existing_map = {clean(r[0]).upper(): i+1 for i, r in enumerate(all_vals[1:]) if r and r[0]}
    for setting, val in defaults:
        row = existing_map.get(setting.upper())
        if row:
            if not all_vals[row-1][1] if len(all_vals[row-1])>1 else True:
                ws.update_cell(row, 2, val)
        else:
            ws.append_row([setting, val], value_input_option="RAW")
    ws.format("A1:B1", {"textFormat":{"bold":True}})
    return "created" if not existing or not existing[0] else "updated"

def _create_tag_options_tab(sh) -> str:
    ws = _get_or_create_tab(sh, "Tag Options", cols=2, rows=100)
    existing = ws.row_values(1)
    if not existing or existing[0] != "FIELD":
        ws.update("A1:B1", [["FIELD","VALUE"]], value_input_option="RAW")
    # Build all rows
    rows = [["FIELD","VALUE"]]
    for field, values in DROP_DOWN_VALUES.items():
        for v in values:
            rows.append([field, v])
    ws.clear()
    ws.update("A1", rows, value_input_option="RAW")
    ws.format("A1:B1", {"textFormat":{"bold":True}})
    return "created" if not existing or not existing[0] else "updated"

def _create_migration_log_tab(sh) -> str:
    ws = _get_or_create_tab(sh, "Migration Log", cols=7, rows=10)
    existing = ws.row_values(1)
    log_cols = ["TIMESTAMP","SOURCE_FILE","ROWS_READ","ROWS_IMPORTED","ROWS_SKIPPED","DUPLICATES_FOUND","ERRORS"]
    if not existing or existing[0] != "TIMESTAMP":
        ws.update("A1", [log_cols], value_input_option="RAW")
    ws.format("A1:G1", {"textFormat":{"bold":True}})
    return "created" if not existing or not existing[0] else "updated"

# ============================================================================
# OLD DATA IMPORT -port existing spreadsheets into Google Sheet
# ============================================================================
def _find_old_files() -> List[str]:
    """Find old spreadsheet files in the repo (above project dir)."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = []
    import glob
    for pat in ["**/*.xlsx","**/*.xls","**/*.csv"]:
        for f in glob.glob(os.path.join(base, pat), recursive=True):
            fa = os.path.abspath(f)
            if "__pycache__" in fa or "speakhire-outreach" in fa:
                continue
            files.append(fa)
    return sorted(set(files))

def _map_old_row(raw: dict, headers: List[str]) -> dict:
    """Map old spreadsheet row to TRACKER_COLUMNS schema."""
    rd = {c:"" for c in TRACKER_COLUMNS}
    # Column name mapping
    col_map = {
        "ASSOCIATION NAME":"ORG_NAME","ASSOCIATION_NAME":"ORG_NAME",
        "COLLEGE":"ORG_NAME","SCHOOL":"ORG_NAME","SECONDARY SCHOOLS":"ORG_NAME",
        "ORGANIZATION":"ORG_NAME","ORGANISATION":"ORG_NAME","ORG NAME":"ORG_NAME",
        "FULL NAME":"RECIPIENT","FULL_NAME":"RECIPIENT","NAME":"RECIPIENT",
        "FIRST NAME":"CONTACT_FIRST_NAME","FIRST_NAME":"CONTACT_FIRST_NAME",
        "LAST NAME":"CONTACT_LAST_NAME","LAST_NAME":"CONTACT_LAST_NAME",
        "EMAIL":"EMAIL","ORG EMAIL":"EMAIL","ORGANISATION EMAIL":"EMAIL",
        "WEBSITE":"ORG_WEBSITE","URL":"ORG_WEBSITE","LINK":"ORG_WEBSITE",
        "NOTES":"NOTES","NOTES/ISSUES":"NOTES","NOTES_ISSUES":"NOTES",
        "STATUS":"STATUS","TYPE":"ORG_TYPE","TYPE_OF_ASSOCIATION":"ORG_TYPE",
        "REACHED_OUT?":"NOTES","REACHED_OUT":"NOTES","RESPONSE":"NOTES",
        "DATE":"SENT_AT",
    }
    for i, h in enumerate(headers):
        val = clean(raw.get(i, raw.get(h, "")))
        if not val: continue
        key = col_map.get(h.upper().replace(" ","_").replace("-","_").replace("/","_"))
        if not key:
            for c in TRACKER_COLUMNS:
                if c.replace("_","") == h.upper().replace(" ","").replace("-","").replace("/","").replace("_",""):
                    key = c; break
        if key and key in rd:
            if not rd[key]: rd[key] = val
    # Derive RECIPIENT
    if not rd["RECIPIENT"]:
        first = rd.get("CONTACT_FIRST_NAME","")
        last = rd.get("CONTACT_LAST_NAME","")
        if first and last: rd["RECIPIENT"] = f"{first} {last}"
        elif first: rd["RECIPIENT"] = first
        else: rd["RECIPIENT"] = "Partnerships Team"
    # Default STATUS
    if not rd["STATUS"]:
        rd["STATUS"] = "READY_FOR_RESEARCH"
    # Default sender
    if not rd["SENDER_NAME"]:
        rd["SENDER_NAME"] = DEFAULT_SENDER_NAME
    if not rd["SENDER_ORG"]:
        rd["SENDER_ORG"] = DEFAULT_SENDER_ORG
    rd["CTA_TYPE"] = rd["CTA_TYPE"] or "SPONSORSHIP_CALL"
    rd["CALL_DURATION"] = rd["CALL_DURATION"] or "15-20 minutes"
    rd["OPT_OUT"] = rd["OPT_OUT"] or "FALSE"
    return rd

def _dedup_key(row: dict) -> str:
    email = clean(row.get("EMAIL","")).lower()
    org = clean(row.get("ORG_NAME","")).lower()
    web = clean(row.get("ORG_WEBSITE","")).lower()
    if email: return f"email:{email}"
    if org and web: return f"org_web:{org}|{web}"
    if org: return f"org:{org}"
    return ""

def _deduplicate_import(incoming: List[Dict], existing: List[Dict]) -> Tuple[List[Dict], int]:
    exist_keys = {}
    for i, r in enumerate(existing):
        k = _dedup_key(r)
        if k: exist_keys[k] = i
    merged = []
    dupes = 0
    for row in incoming:
        k = _dedup_key(row)
        if k and k in exist_keys and exist_keys[k] < len(existing):
            # merge: fill blank existing fields
            ex = existing[exist_keys[k]]
            changed = False
            for c in TRACKER_COLUMNS:
                if not clean(ex.get(c,"")) and clean(row.get(c,"")):
                    ex[c] = row[c]
                    changed = True
            if changed:
                merged.append(ex)
            dupes += 1
        else:
            merged.append(row)
    return merged, dupes

def _log_migration(sh, source_file: str, rows_read: int, imported: int, skipped: int, dupes: int, errs: str) -> None:
    ws = _get_or_create_tab(sh, "Migration Log", cols=7, rows=100)
    ws.append_row([ts_now(), source_file, rows_read, imported, skipped, dupes, errs], value_input_option="RAW")

def import_old_spreadsheets(sheet_url: str = "") -> Dict:
    """Scan repo for old spreadsheets and import contacts into Outreach Tracker."""
    sh = _get_gsheet(sheet_url)
    ws_tracker = _get_or_create_tab(sh, "Outreach Tracker", cols=len(TRACKER_COLUMNS))
    # ensure tracker columns
    _create_tracker_tab(sh)
    # read existing rows
    all_vals = ws_tracker.get_all_values()
    existing_rows = []
    if len(all_vals) > 1:
        hdrs = [h.strip().upper().replace(" ","_") for h in all_vals[0]]
        for ri, row in enumerate(all_vals[1:], start=2):
            rd = {c:"" for c in TRACKER_COLUMNS}
            for i, h in enumerate(all_vals[0]):
                key = _norm_hdr(h)
                if key in rd:
                    rd[key] = clean(row[i] if i < len(row) else "")
            rd["_row"] = ri
            existing_rows.append(rd)

    total_imported = 0
    total_read = 0
    total_dupes = 0
    total_errors = 0

    for old_file in _find_old_files():
        # refresh existing rows each iteration for accurate dedup
        all_vals = ws_tracker.get_all_values()
        existing_rows = []
        if len(all_vals) > 1:
            for ri, row in enumerate(all_vals[1:], start=2):
                rd = {c:"" for c in TRACKER_COLUMNS}
                for i, h in enumerate(all_vals[0]):
                    key = _norm_hdr(h)
                    if key in rd:
                        rd[key] = clean(row[i] if i < len(row) else "")
                rd["_row"] = ri
                existing_rows.append(rd)
        log.info(f"Importing: {old_file}")
        try:
            if old_file.endswith(".csv"):
                df = pd.read_csv(old_file)
                sheets = {"Sheet1": df}
            else:
                sheets = pd.read_excel(old_file, sheet_name=None)
        except Exception as e:
            log.warning(f"Failed to read {old_file}: {e}")
            _log_migration(sh, old_file, 0, 0, 0, 0, str(e)[:200])
            total_errors += 1
            continue

        for sn, df in sheets.items():
            if df.empty: continue
            df = df.dropna(how="all")
            if len(df.columns) < 2: continue
            headers = [str(h).strip() for h in df.columns]
            incoming = []
            for _, row in df.iterrows():
                raw = {headers[i]: str(row.iloc[i]) if i < len(row) and pd.notna(row.iloc[i]) else ""
                       for i in range(len(headers))}
                mapped = _map_old_row(raw, headers)
                if mapped["ORG_NAME"] or mapped["EMAIL"]:
                    incoming.append(mapped)
            if not incoming: continue
            total_read += len(incoming)
            merged, dupes = _deduplicate_import(incoming, existing_rows)
            total_dupes += dupes
            # batch append new rows
            batch = []
            for row in merged:
                if row.get("_row"): continue
                batch.append([str(row.get(c,"")) for c in TRACKER_COLUMNS])
                total_imported += 1
            if batch:
                try:
                    ws_tracker.append_rows(batch, value_input_option="RAW")
                except Exception:
                    # fallback: one by one with delay
                    for b in batch:
                        ws_tracker.append_row(b, value_input_option="RAW")
            _log_migration(sh, f"{old_file}:{sn}", len(incoming), len(merged), len(incoming)-len(merged), dupes, "")

    # Reset sheet1 if needed (rename default sheet)
    try:
        default_ws = sh.sheet1
        if default_ws.title not in ("Outreach Tracker","Settings","Tag Options","Migration Log"):
            # Just leave it; don't delete
            pass
    except Exception: pass

    return {"status":"success","files_scanned":len(_find_old_files()),
            "rows_read":total_read,"rows_imported":total_imported,
            "duplicates":total_dupes,"errors":total_errors}

def init_and_import(sheet_url: str = "") -> Dict:
    init = initialise_google_sheet(sheet_url)
    imp = import_old_spreadsheets(sheet_url)
    return {"status":"success","init":init,"import":imp}

# ============================================================================
# WEBSITE RESEARCH -primary research source (free, always available)
# ============================================================================

_BOT_HEADERS = {"User-Agent": "Mozilla/5.0 (SpeakHire Outreach Bot; nonprofit use)"}

_USEFUL_PATH_PATTERNS = [
    "about", "about-us", "who-we-are", "our-story", "mission",
    "programs", "what-we-do", "our-work", "services", "initiatives",
    "news", "blog", "events", "impact", "stories",
    "contact", "get-involved", "donate",
    "community", "youth", "education", "immigrant", "inclusion",
]

def fetch_url_text(url: str, timeout: int = 15) -> dict:
    """
    Fetch a webpage and return structured data.
    Returns: {"url": final_url, "title": str, "text": str, "links": list, "error": str}
    Never crashes; returns error info on failure.
    """
    result = {"url": url, "title": "", "text": "", "links": [], "error": ""}
    if not url:
        result["error"] = "No URL provided"
        return result
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url
        result["url"] = url
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(url, timeout=timeout, headers=_BOT_HEADERS, allow_redirects=True)
        resp.raise_for_status()
        result["url"] = resp.url  # final URL after redirects
        soup = BeautifulSoup(resp.text, "html.parser")

        # title
        title_tag = soup.find("title")
        if title_tag:
            result["title"] = title_tag.get_text(strip=True)

        # clean visible text
        for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            t.decompose()
        result["text"] = soup.get_text(separator=" ", strip=True)[:8000]

        # extract useful internal links
        base_domain = domain_from_url(resp.url)
        links_seen = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            if not href or href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
                continue
            # resolve relative URLs
            from urllib.parse import urljoin
            full = urljoin(resp.url, href)
            link_domain = domain_from_url(full)
            link_text = a.get_text(strip=True)[:120]
            # only include same-domain links
            if link_domain == base_domain and full not in links_seen:
                links_seen.add(full)
                result["links"].append({"text": link_text, "url": full})

    except Exception as e:
        result["error"] = str(e)[:200]
        log.debug(f"fetch_url_text failed for {url}: {e}")
    return result


def _score_link(link: dict) -> int:
    """Score an internal link for research relevance."""
    url_lower = link["url"].lower() + link["text"].lower()
    score = 0
    for pat in _USEFUL_PATH_PATTERNS:
        if pat in url_lower:
            score += 3
    # prefer shorter paths (closer to root = more important)
    path = urlparse(link["url"]).path
    score -= len(path) // 20
    # prefer links with descriptive text
    if len(link["text"]) > 3:
        score += 2
    return score


def crawl_org_website(website: str, contact_page: str = "") -> dict:
    """
    Crawl the organisation's official website as the primary research source.

    Fetches homepage (or contact page), discovers useful internal links,
    fetches up to 5 most relevant subpages, and returns combined text.

    Returns: {"pages": [fetch_url_text results], "combined_text": str,
              "has_website": bool, "error": str}
    """
    result = {"pages": [], "combined_text": "", "has_website": False, "error": ""}
    start_url = website or contact_page
    if not start_url:
        result["error"] = "No ORG_WEBSITE or CONTACT_PAGE_URL configured"
        return result

    result["has_website"] = True

    # Fetch the starting page
    main_page = fetch_url_text(start_url)
    result["pages"].append(main_page)
    if main_page["error"]:
        result["error"] = f"Failed to fetch {start_url}: {main_page['error']}"
        result["combined_text"] = main_page.get("text", "")
        return result

    # Score and select the best internal links to crawl
    candidates = [(lnk, _score_link(lnk)) for lnk in main_page.get("links", [])
                  if _score_link(lnk) > 0]
    candidates.sort(key=lambda x: x[1], reverse=True)

    # Fetch up to 5 best subpages (excluding the homepage itself)
    fetched = set([main_page["url"].rstrip("/")])
    for link, score in candidates[:8]:
        if len(result["pages"]) >= 6:  # homepage + 5 subpages
            break
        url = link["url"].rstrip("/")
        if url in fetched:
            continue
        fetched.add(url)
        page = fetch_url_text(link["url"])
        result["pages"].append(page)
        if page["error"]:
            log.debug(f"Subpage failed: {link['url']} -{page['error']}")

    # Combine all text
    texts = []
    for p in result["pages"]:
        if p["title"]:
            texts.append(f"[PAGE: {p['title']}] {p['text']}")
        else:
            texts.append(p["text"])
    result["combined_text"] = "\n\n---\n\n".join(texts)[:12000]

    log.info(f"Website crawl: fetched {len(result['pages'])} pages ({len(result['combined_text'])} chars)")
    return result


def infer_org_from_website(website: str, contact_page: str = "") -> Dict[str, str]:
    """
    Given an ORG_WEBSITE or CONTACT_PAGE_URL, fetch the page and infer:
    ORG_NAME, ORG_TYPE, SEGMENT, and public EMAIL from page metadata.
    """
    url_to_fetch = website or contact_page
    if not url_to_fetch:
        return {}
    if not url_to_fetch.startswith("http"):
        url_to_fetch = "https://" + url_to_fetch

    result = {}
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(url_to_fetch, timeout=15, headers=_BOT_HEADERS)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # --- infer ORG_NAME ---
        org_name = ""
        title_tag = soup.find("title")
        if title_tag:
            title_text = title_tag.get_text(strip=True)
            for sep in (" -", " | ", " - ", " :: ", " – "):
                if sep in title_text:
                    parts = [p.strip() for p in title_text.rsplit(sep, 1)]
                    if 2 < len(parts[1]) < 60:
                        org_name = parts[1]
                    elif len(parts[0]) < 60:
                        org_name = parts[0]
                    break
            if not org_name:
                org_name = title_text[:80]

        if not org_name or len(org_name) < 3:
            og = soup.find("meta", property="og:site_name")
            if og and og.get("content"):
                org_name = og["content"].strip()

        if not org_name or len(org_name) < 3:
            mt = soup.find("meta", attrs={"name": "title"})
            if mt and mt.get("content"):
                org_name = mt["content"].strip()

        if not org_name or len(org_name) < 3:
            domain = domain_from_url(url_to_fetch)
            org_name = domain.split(".")[0].replace("-", " ").replace("_", " ").title()

        result["ORG_NAME"] = clean(org_name)[:120]
        log.info(f"Inferred ORG_NAME='{result['ORG_NAME']}' from {url_to_fetch}")

        # --- infer ORG_TYPE ---
        page_text = soup.get_text(separator=" ", strip=True).lower()[:5000]
        type_hints = {
            "nonprofit": ["nonprofit", "non-profit", "501(c)(3)", "charity", "charitable"],
            "university": ["university", "college", "faculty", "student affairs"],
            "school": ["school", "high school", "elementary", "secondary", "k-12"],
            "company": ["corporation", "inc.", "llc", "startup", "company"],
            "association": ["association", "society", "coalition", "alliance"],
            "foundation": ["foundation", "grantmaking", "philanthropy"],
            "government": ["government", "department", "agency", "municipal", "city of"],
            "community group": ["community", "grassroots", "mutual aid", "collective"],
        }
        for ttype, keywords in type_hints.items():
            if any(kw in page_text for kw in keywords):
                result["ORG_TYPE"] = ttype
                break

        # --- infer SEGMENT ---
        seg_hints = {
            "youth empowerment": ["youth", "young people", "teen", "adolescent"],
            "immigrant community": ["immigrant", "refugee", "asylum", "migrant", "newcomer"],
            "education": ["education", "school", "learning", "student", "academic"],
            "DEI": ["diversity", "equity", "inclusion", "belonging", "dei"],
            "workforce development": ["workforce", "career", "employment", "job training"],
            "cultural identity": ["cultural", "heritage", "language", "identity", "ethnic"],
            "corporate CSR": ["csr", "social responsibility", "corporate giving"],
        }
        for seg, keywords in seg_hints.items():
            if any(kw in page_text for kw in keywords):
                result["SEGMENT"] = seg
                break

        # --- find public email ---
        found_emails = set()
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if href.startswith("mailto:"):
                em = href[7:].split("?")[0].strip()
                if validate_email(em):
                    found_emails.add(em)
        for em in re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}").findall(page_text):
            if validate_email(em):
                found_emails.add(em.lower())
        best = None
        for em in found_emails:
            if best is None:
                best = em
            else:
                best = em
                break
        if best:
            result["EMAIL"] = best
            log.info(f"Found public email: {best}")

    except Exception as e:
        log.warning(f"Website inference failed for {url_to_fetch}: {e}")

    return result

# ============================================================================
# SEARCH -secondary/optional, only if SEARCH_PROVIDER configured
# ============================================================================
def _has_search() -> bool:
    p = SEARCH_PROVIDER.lower()
    if p == "serper": return bool(SERPER_API_KEY)
    if p == "tavily": return bool(TAVILY_API_KEY)
    if p == "serpapi": return bool(SERPAPI_API_KEY)
    return False

def search_web(query: str, num: int = 5) -> List[Dict]:
    p = SEARCH_PROVIDER.lower()
    if p == "serper" and SERPER_API_KEY:
        return _serper(query, num)
    elif p == "tavily" and TAVILY_API_KEY:
        return _tavily(query, num)
    elif p == "serpapi" and SERPAPI_API_KEY:
        return _serpapi(query, num)
    return []

def _serper(q, n):
    r = requests.post("https://google.serper.dev/search",
        headers={"X-API-KEY":SERPER_API_KEY,"Content-Type":"application/json"},
        json={"q":q,"num":n}, timeout=15)
    r.raise_for_status()
    return [{"title":i.get("title",""),"snippet":i.get("snippet",""),
             "url":i.get("link",""),"date":i.get("date","")}
            for i in r.json().get("organic",[])[:n]]

def _tavily(q, n):
    r = requests.post("https://api.tavily.com/search",
        json={"api_key":TAVILY_API_KEY,"query":q,"search_depth":"basic","max_results":n}, timeout=15)
    r.raise_for_status()
    return [{"title":i.get("title",""),"snippet":i.get("content",""),
             "url":i.get("url",""),"date":i.get("published_date","")}
            for i in r.json().get("results",[])[:n]]

def _serpapi(q, n):
    r = requests.get("https://serpapi.com/search",
        params={"q":q,"api_key":SERPAPI_API_KEY,"num":n,"engine":"google"}, timeout=15)
    r.raise_for_status()
    return [{"title":i.get("title",""),"snippet":i.get("snippet",""),
             "url":i.get("link",""),"date":i.get("date","")}
            for i in r.json().get("organic_results",[])[:n]]

def research(org_name: str, org_website: str = "") -> Tuple[str, List[Dict], str]:
    """
    Primary: crawl the official website (free, always works).
    Secondary: use external search API if configured.

    Returns (primary_query, search_results, combined_website_text).
    """
    if not org_name:
        return "", [], ""

    website_text = ""
    has_search = _has_search()
    search_results = []

    # --- PRIMARY: crawl official website ---
    if org_website:
        crawl = crawl_org_website(org_website)
        website_text = crawl["combined_text"]
        if not website_text and crawl["error"]:
            log.warning(f"Website crawl failed: {crawl['error']}")
    elif not has_search:
        # No website AND no search API -truly no research source
        return "", [], ""

    # --- SECONDARY: external search API (supplements website data) ---
    if has_search:
        domain = domain_from_url(org_website) if org_website else ""
        queries = [
            f'"{org_name}" recent event community youth diversity inclusion',
            f'"{org_name}" immigrant belonging identity program',
            f'"{org_name}" partnership sponsorship nonprofit',
        ]
        if domain:
            queries.append(f"site:{domain} event OR program OR initiative OR news")
        for q in queries:
            results = search_web(q, num=3)
            search_results.extend(results)
            if len(search_results) >= 10:
                break
        seen, unique = set(), []
        for r in search_results:
            u = r.get("url","")
            if u and u not in seen:
                seen.add(u)
                unique.append(r)
        search_results = unique[:10]

    # Build query string for logging
    query_str = f"website crawl ({org_website})"
    if has_search:
        query_str += " + search API"
    elif not org_website:
        query_str = "(no research source)"

    return query_str, search_results, website_text

# ============================================================================
# EVIDENCE VALIDATION
# ============================================================================
def validate_evidence(draft: Dict, search_results: List[Dict], website_text: str, org_name: str) -> Dict:
    """
    If EVIDENCE_CONFIDENCE=HIGH, require real evidence.
    If checks fail, downgrade confidence.
    """
    conf = draft.get("evidence_confidence","").upper()
    if conf != "HIGH":
        return draft

    source_url = draft.get("source_url","")
    evidence_title = draft.get("evidence_title","")

    # Check 1: SOURCE_URL must exist
    if not source_url:
        log.warning(f"HIGH confidence but no SOURCE_URL -downgrading to MEDIUM")
        draft["evidence_confidence"] = "MEDIUM"
        return draft

    # Check 2: EVIDENCE_TITLE must exist
    if not evidence_title:
        log.warning(f"HIGH confidence but no EVIDENCE_TITLE -downgrading to MEDIUM")
        draft["evidence_confidence"] = "MEDIUM"
        return draft

    # Check 3: SOURCE_URL should appear in search results or be the org's site
    source_domain = domain_from_url(source_url)
    org_domains = set()
    search_urls = [r.get("url","") for r in search_results]
    search_text = " ".join([r.get("snippet","") + r.get("title","") for r in search_results]) + (website_text or "")

    # is source_url in search results?
    found_in_search = any(source_url in su or su in source_url for su in search_urls)
    # is source_url the org's own site or a known related source?
    found_in_website = (website_text and (source_domain in website_text.lower() or source_url.lower() in website_text.lower()))

    if not found_in_search and not found_in_website:
        log.warning(f"SOURCE_URL '{source_url}' not found in search results -downgrading to MEDIUM")
        draft["evidence_confidence"] = "MEDIUM"

    # Check 4: personalised_opener should not invent unsupported facts
    opener = draft.get("personalised_opener","")
    evidence_title_lower = evidence_title.lower()
    if evidence_title_lower and evidence_title_lower not in opener.lower():
        # The opener might still be valid, but flag if it seems invented
        pass  # not blocking -just a soft check

    return draft

# ============================================================================
# LLM DRAFT GENERATION
# ============================================================================
def _format_search_results(results: List[Dict]) -> str:
    if not results: return "(No search results available)"
    lines = []
    for i, r in enumerate(results[:8], 1):
        lines.append(f"{i}. Title: {r.get('title','')}")
        lines.append(f"   Snippet: {r.get('snippet','')}")
        lines.append(f"   URL: {r.get('url','')}")
        lines.append(f"   Date: {r.get('date','')}")
        lines.append("")
    return "\n".join(lines)

def _fallback_draft(org_name: str, recipient: str, has_email: bool,
                    sender_name: str = "Hana", sender_org: str = "SpeakHire",
                    sender_title: str = "", error: str = "") -> Dict:
    greeting = recipient or "Partnerships Team"
    intro = f"I'm {sender_name} with {sender_org}, a NYC-based nonprofit supporting underrepresented, primarily immigrant youth and communities in achieving economic mobility."
    sig_lines = [f"Best,", sender_name]
    if sender_title:
        sig_lines.append(sender_title)
    sig_lines.append(sender_org)
    signature = "\n".join(sig_lines)
    return {
        "evidence_title":"","evidence_summary":"","source_url":"","source_date":"",
        "relevant_theme":"youth empowerment, community inclusion",
        "evidence_confidence":"LOW",
        "personalised_opener":(
            f"I came across {org_name} and thought there could be meaningful alignment "
            f"with {sender_org}'s work around identity, belonging, youth empowerment, and community inclusion."
        ),
        "email_subject":f"Exploring a #SpeakingMyName partnership with {org_name}",
        "email_draft":(
            f"Dear {greeting},\n\n"
            f"I came across {org_name} and thought there could be meaningful alignment "
            f"with {sender_org}'s work around identity, belonging, youth empowerment, and community inclusion.\n\n"
            f"{intro}\n\n"
            f"We are currently building momentum for #SpeakingMyName, our campaign where participants "
            f"record a short video sharing their name, its pronunciation, and the story behind it. "
            f"The campaign is about helping people, especially those from diverse communities whose names "
            f"are often mispronounced, feel a stronger sense of belonging.\n\n"
            f"Given {org_name}'s work in this space, we would love to explore whether there may be "
            f"an opportunity to collaborate through sponsorship, outreach, or community engagement.\n\n"
            f"Would you be open to a short 15-20 minute call next week to discuss whether this could be a good fit?\n\n"
            f"Thank you for your time and consideration.\n\n"
            f"{signature}"
        ),
        "review_status":"NEEDS_REVIEW" if has_email else "NEEDS_CONTACT_INFO",
        "error":error,
    }

def _call_llm_direct(system_prompt: str, user_prompt: str) -> str:
    """Direct HTTP call to DeepSeek/OpenAI-compatible API (no langchain needed)."""
    url = (LLM_BASE_URL or "https://api.openai.com/v1") + "/chat/completions"
    resp = requests.post(url,
        headers={"Authorization":f"Bearer {LLM_API_KEY}","Content-Type":"application/json"},
        json={
            "model": MODEL_NAME,
            "messages": [
                {"role":"system","content":system_prompt},
                {"role":"user","content":user_prompt},
            ],
            "temperature": 0.2,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

def generate_draft(
    org_name: str, recipient: str, org_type: str, segment: str,
    search_results: List[Dict], website_text: str, has_email: bool,
    sender_name: str = "Hana", sender_org: str = "SpeakHire", sender_title: str = "",
) -> Dict:
    """Generate a structured draft via LLM using langchain structured output or fallback."""
    if not LLM_API_KEY:
        log.warning("No LLM API key set -using fallback draft")
        return _fallback_draft(org_name, recipient, has_email, sender_name, sender_org, sender_title,
                               "No LLM API key configured. Add DEEPSEEK_API_KEY to .env to generate AI drafts.")

    # Build the context: website text is primary, search results are secondary
    context_parts = []
    if website_text:
        context_parts.append(f"--- OFFICIAL WEBSITE CONTENT (primary source) ---\n{website_text[:5000]}")
    if search_results:
        context_parts.append(f"--- EXTERNAL SEARCH RESULTS (supplementary) ---\n{_format_search_results(search_results)}")
    if not context_parts:
        context_parts.append("(No research sources available -generate a generic mission-alignment draft only.)")
    results_text = "\n\n".join(context_parts)

    intro_line = f"I'm {sender_name} with {sender_org}, a NYC-based nonprofit supporting underrepresented, primarily immigrant youth and communities in achieving economic mobility."
    sig_lines = [f"Best,", sender_name]
    if sender_title:
        sig_lines.append(sender_title)
    sig_lines.append(sender_org)
    signature_block = "\n".join(sig_lines)

    user_prompt = f"""Organisation: {org_name}
Organisation Type: {org_type or 'Unknown'}
Segment: {segment or 'Unknown'}
Recipient greeting: {recipient or 'Partnerships Team'}
Has email address: {has_email}

SENDER INFORMATION -use EXACTLY this:
  Sender name: {sender_name}
  Sender organisation: {sender_org}
  Intro line (use verbatim): {intro_line}
  Sign-off (use verbatim):
{signature_block}

Research context:
{results_text}

Generate the draft email. Return ONLY valid JSON (no markdown, no extra text)."""

    # --- Try LangChain structured output first ---
    try:
        from langchain_openai import ChatOpenAI

        kwargs = dict(api_key=LLM_API_KEY, model=MODEL_NAME, temperature=0.2)
        if LLM_BASE_URL:
            kwargs["base_url"] = LLM_BASE_URL
        llm = ChatOpenAI(**kwargs)

        # try structured output
        try:
            structured_llm = llm.with_structured_output(OutreachDraft)
            result = structured_llm.invoke([
                ("system", SYSTEM_PROMPT),
                ("human", user_prompt),
            ])
            draft = result.model_dump()
            log.info("Used langchain structured output")
            return validate_evidence(draft, search_results, website_text, org_name)
        except Exception as e:
            log.warning(f"Structured output failed ({e}), falling back to JSON parsing")

        # fallback: plain invoke + json parse
        from langchain_core.messages import HumanMessage, SystemMessage
        response = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])
        return _parse_json_response(response.content, search_results, website_text,
                                     org_name, has_email, recipient, sender_name, sender_org, sender_title)
    except ImportError:
        log.info("langchain not installed -using direct HTTP call")
    except Exception as e:
        log.warning(f"LangChain failed ({e}), falling back to direct HTTP")

    # --- Fallback: direct HTTP + JSON parsing ---
    try:
        content = _call_llm_direct(SYSTEM_PROMPT, user_prompt)
        return _parse_json_response(content, search_results, website_text,
                                     org_name, has_email, recipient, sender_name, sender_org, sender_title)
    except Exception as e:
        log.error(f"LLM call failed: {e}")
        return _fallback_draft(org_name, recipient, has_email, sender_name, sender_org, sender_title, str(e))


def _parse_json_response(
    content: str, search_results: List[Dict], website_text: str,
    org_name: str, has_email: bool, recipient: str,
    sender_name: str = "Hana", sender_org: str = "SpeakHire", sender_title: str = "",
) -> Dict:
    """Parse LLM JSON response, validate with Pydantic, retry on failure."""
    content = content.strip()
    for prefix in ("```json", "```"):
        if content.startswith(prefix):
            content = content[len(prefix):].strip()
    if content.endswith("```"):
        content = content[:-3].strip()
    s, e = content.find("{"), content.rfind("}")
    if s != -1 and e != -1:
        content = content[s:e+1]

    # try parsing + Pydantic validation
    for attempt in range(2):
        try:
            raw = json.loads(content)
            draft = OutreachDraft(**raw)
            result = draft.model_dump()
            result = validate_evidence(result, search_results, website_text, org_name)
            if not result.get("error"):
                return result
            # if validation found errors, still return it
            return result
        except (json.JSONDecodeError, ValidationError) as e:
            log.warning(f"JSON parse/validation attempt {attempt+1} failed: {e}")
            if attempt == 0:
                # retry: call LLM again with stricter prompt
                try:
                    retry_prompt = f"""Your previous response was not valid JSON or failed validation.
Please respond with VALID JSON only, exactly matching this schema:
{json.dumps(OutreachDraft.model_json_schema(), indent=2)}

Organisation: {org_name}
Recipient: {recipient or 'Partnerships Team'}
Has email: {has_email}

Return ONLY the JSON object, nothing else."""
                    content = _call_llm_direct(SYSTEM_PROMPT[:500], retry_prompt)
                    content = content.strip()
                    if content.startswith("```"):
                        content = content[content.find("\n"):].strip()
                    if content.endswith("```"):
                        content = content[:-3].strip()
                    s2, e2 = content.find("{"), content.rfind("}")
                    if s2 != -1 and e2 != -1:
                        content = content[s2:e2+1]
                except Exception:
                    pass
    # both attempts failed
    return _fallback_draft(org_name, recipient, has_email, sender_name, sender_org, sender_title,
                           "LLM response could not be parsed as valid JSON after retry")

# ============================================================================
# WORKFLOW: GENERATE DRAFTS
# ============================================================================
def generate_drafts(row_number: int = None, mode: str = None, sheet_url: str = "",
                    force: bool = False) -> Dict:
    rows = _read_rows(sheet_url, mode)
    processed, errors = [], []
    has_search = _has_search()

    for row in rows:
        status = row.get("STATUS","").upper()
        if status != "READY_FOR_RESEARCH":
            if force and row_number is not None and row["_row"] == row_number and status in ("DRAFTED","ERROR"):
                pass  # allow forced re-generation
            else:
                continue
        if is_opt_out(row):
            continue
        if status in ("SENT","SKIP") and not (force and row["_row"] == row_number):
            continue
        if row_number is not None and row["_row"] != row_number:
            continue

        rn = row["_row"]
        org_name = row.get("ORG_NAME","")
        org_website = row.get("ORG_WEBSITE","")
        contact_page = row.get("CONTACT_PAGE_URL","")
        recipient = row.get("RECIPIENT","") or row.get("CONTACT_FIRST_NAME","")
        email = row.get("EMAIL","")
        has_email = bool(email and validate_email(email))

        # --- LINK-ONLY ROW: infer ORG_NAME if blank ---
        if not org_name and (org_website or contact_page):
            log.info(f"Row {rn}: ORG_NAME blank -inferring from website...")
            inferred = infer_org_from_website(org_website, contact_page)
            if inferred.get("ORG_NAME"):
                row["ORG_NAME"] = org_name = inferred["ORG_NAME"]
                row["ORG_TYPE"] = row["ORG_TYPE"] or inferred.get("ORG_TYPE","")
                row["SEGMENT"] = row["SEGMENT"] or inferred.get("SEGMENT","")
                if not row.get("EMAIL") and inferred.get("EMAIL"):
                    row["EMAIL"] = email = inferred["EMAIL"]
                    has_email = bool(email and validate_email(email))

        if not org_name:
            row["STATUS"] = "ERROR"
            row["ERROR"] = "No ORG_NAME available and could not infer from website"
            errors.append({"row":rn,"org":"(unknown)","error":row["ERROR"]})
            continue

        log.info(f"Row {rn}: Generating draft for '{org_name}'")

        sender_name = get_sender_name(row)
        sender_org = get_sender_org(row)
        sender_title = get_sender_title(row)

        try:
            query, results, website_text = research(org_name, org_website)
            draft = generate_draft(
                org_name, recipient,
                row.get("ORG_TYPE",""), row.get("SEGMENT",""),
                results, website_text, has_email,
                sender_name, sender_org, sender_title,
            )
        except Exception as e:
            log.error(f"Row {rn}: Error: {e}")
            draft = _fallback_draft(org_name, recipient, has_email,
                                     sender_name, sender_org, sender_title, str(e))
            query = ""
            results = []
            website_text = ""

        # --- Set review status based on email availability ---
        if not row.get("EMAIL"):
            draft["review_status"] = "NEEDS_CONTACT_INFO"

        row["RESEARCH_QUERY"] = query
        row["EVIDENCE_TITLE"] = draft.get("evidence_title","")
        row["EVIDENCE_SUMMARY"] = draft.get("evidence_summary","")
        row["SOURCE_URL"] = draft.get("source_url","")
        row["SOURCE_DATE"] = draft.get("source_date","")
        row["RELEVANT_THEME"] = draft.get("relevant_theme","")
        row["EVIDENCE_CONFIDENCE"] = draft.get("evidence_confidence","")
        row["PERSONALISED_OPENER"] = draft.get("personalised_opener","")
        row["EMAIL_SUBJECT"] = draft.get("email_subject","")
        row["EMAIL_DRAFT"] = draft.get("email_draft","")
        row["CTA_TYPE"] = "SPONSORSHIP_CALL"
        row["CALL_DURATION"] = "15-20 minutes"

        if draft.get("error"):
            row["STATUS"] = "ERROR"
            row["ERROR"] = draft.get("error","")
            errors.append({"row":rn,"org":org_name,"error":draft["error"]})
        else:
            row["STATUS"] = "DRAFTED"
            row["ERROR"] = ""
            if not website_text and not results:
                if not org_website and not contact_page and not _has_search():
                    row["ERROR"] = "No research source configured. Add ORG_WEBSITE or configure SEARCH_PROVIDER."
            processed.append({"row":rn,"org":org_name})

    _write_rows(rows, sheet_url, mode)
    return {"status":"success","processed":processed,"errors":errors,
            "total_processed":len(processed),"total_errors":len(errors)}

# ============================================================================
# WORKFLOW: AUTO-APPROVE
# ============================================================================
def auto_approve(min_confidence: str = "HIGH", mode: str = None, sheet_url: str = "") -> Dict:
    rows = _read_rows(sheet_url, mode)
    approved = []
    for row in rows:
        if row.get("STATUS","").upper() != "DRAFTED": continue
        if is_opt_out(row): continue
        if row.get("EVIDENCE_CONFIDENCE","").upper() != min_confidence.upper(): continue
        if not row.get("SOURCE_URL"): continue
        if not row.get("EMAIL_DRAFT"): continue
        if not row.get("EMAIL"): continue
        if row.get("ERROR"): continue
        row["STATUS"] = "APPROVED"
        approved.append({"row":row["_row"],"org":row.get("ORG_NAME","")})
    _write_rows(rows, sheet_url, mode)
    log.info(f"Auto-approved {len(approved)} rows")
    return {"status":"success","approved_count":len(approved),"approved":approved}

# ============================================================================
# WORKFLOW: SEND APPROVED
# ============================================================================
def send_approved(dry_run: bool = None, mode: str = None, sheet_url: str = "") -> Dict:
    if dry_run is None: dry_run = DRY_RUN
    rows = _read_rows(sheet_url, mode)
    sent, skipped, errors = [], [], []

    for row in rows:
        rn = row["_row"]
        sta = row.get("STATUS","").upper()
        email = row.get("EMAIL","")
        human = row.get("HUMAN_EDITED_DRAFT","")
        ai = row.get("EMAIL_DRAFT","")
        body = human or ai

        if sta == "SENT": skipped.append({"row":rn,"reason":"Already SENT"}); continue
        if sta != "APPROVED": skipped.append({"row":rn,"reason":f"STATUS={sta}"}); continue
        if not email: skipped.append({"row":rn,"reason":"No EMAIL"}); continue
        if is_opt_out(row): skipped.append({"row":rn,"reason":"OPT_OUT"}); continue
        if not body: skipped.append({"row":rn,"reason":"No EMAIL_DRAFT"}); continue

        if not dry_run:
            row["STATUS"] = "SENDING"
        display_from = row.get("SEND_FROM") or f"{get_sender_name(row)} from {get_sender_org(row)}"
        result = _send(email, row.get("RECIPIENT",""), row.get("EMAIL_SUBJECT",""), body, dry_run, display_from)

        if dry_run:
            # Don't change STATUS, SENT_AT; just log preview
            row["EMAIL_PROVIDER_STATUS"] = "dry_run_preview"
            sent.append({"row":rn,"email":email,"org":row.get("ORG_NAME",""),"would_send":True})
        elif result["status"] == "SENT":
            row["STATUS"] = "SENT"
            row["SENT_AT"] = result.get("sent_at", ts_now())
            row["EMAIL_PROVIDER_STATUS"] = result.get("provider_status","")
            row["ERROR"] = ""
            sent.append({"row":rn,"email":email,"org":row.get("ORG_NAME","")})
        else:
            row["STATUS"] = "ERROR"
            row["ERROR"] = result.get("message","Unknown error")
            errors.append({"row":rn,"error":result.get("message","")})

    _write_rows(rows, sheet_url, mode)
    return {"status":"success","sent":sent,"skipped":skipped,"errors":errors,
            "total_sent":len(sent),"total_skipped":len(skipped),"total_errors":len(errors),"dry_run":dry_run}

# ============================================================================
# EMAIL SENDING
# ============================================================================
def _send(to_email: str, to_name: str, subject: str, body: str, dry_run: bool,
           display_from: str = "") -> Dict:
    if dry_run:
        log.info(f"[DRY_RUN] Would send to: {to_email} -Subject: {subject}")
        return {"status":"DRY_RUN","message":f"Dry run -would have sent to {to_email}",
                "sent_at":"","provider_status":"dry_run_preview"}

    from_name = display_from or FROM_NAME or "Hana from SpeakHire"

    # --- Gmail SMTP (free) ---
    if USE_GMAIL:
        if not GMAIL_USER or not GMAIL_APP_PASSWORD:
            return {"status":"ERROR","message":"USE_GMAIL=true but GMAIL_USER or GMAIL_APP_PASSWORD not set",
                    "sent_at":"","provider_status":""}
        try:
            import smtplib
            from email.mime.text import MIMEText
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject or "#SpeakingMyName partnership"
            msg["From"] = f"{from_name} <{GMAIL_USER}>"
            msg["To"] = to_email
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
                server.sendmail(GMAIL_USER, [to_email], msg.as_string())
            log.info(f"Email sent via Gmail to {to_email}")
            return {"status":"SENT","message":"Sent via Gmail SMTP",
                    "sent_at":ts_now(),"provider_status":"gmail_ok"}
        except Exception as e:
            log.error(f"Gmail send failed: {e}")
            return {"status":"ERROR","message":str(e),"sent_at":"","provider_status":"gmail_fail"}

    # --- SendGrid ---
    if not SENDGRID_API_KEY:
        return {"status":"ERROR","message":"No email provider configured. Set USE_GMAIL=true or SENDGRID_API_KEY.",
                "sent_at":"","provider_status":""}
    if not FROM_EMAIL:
        return {"status":"ERROR","message":"FROM_EMAIL not set","sent_at":"","provider_status":""}
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, From, To, Subject, PlainTextContent
        msg = Mail(from_email=From(FROM_EMAIL, from_name),
                   to_emails=To(to_email,to_name or "there"),
                   subject=Subject(subject or "#SpeakingMyName partnership"),
                   plain_text_content=PlainTextContent(body))
        r = SendGridAPIClient(SENDGRID_API_KEY).send(msg)
        if 200 <= r.status_code < 300:
            return {"status":"SENT","message":f"Sent HTTP {r.status_code}",
                    "sent_at":ts_now(),"provider_status":str(r.status_code)}
        return {"status":"ERROR","message":f"SendGrid HTTP {r.status_code}",
                "sent_at":"","provider_status":str(r.status_code)}
    except Exception as e:
        log.error(f"Send failed: {e}")
        return {"status":"ERROR","message":str(e),"sent_at":"","provider_status":""}

# ============================================================================
# FASTAPI APP (module-level -works with `uvicorn outreach_worker:app`)
# ============================================================================
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi import HTTPException as FastAPIHTTPException

app = FastAPI(title="SpeakHire Outreach API", version="2.0.0")


async def _check_key(request: Request):
    key = request.headers.get("X-API-Key","")
    if SHEETS_WEBHOOK_API_KEY and key != SHEETS_WEBHOOK_API_KEY:
        raise FastAPIHTTPException(401, "Invalid API key")


class GenReq(BaseModel):
    sheet_url: str = ""
    row_number: int = None
    force: bool = False

class ApproveReq(BaseModel):
    sheet_url: str = ""
    min_confidence: str = "HIGH"

class SendReq(BaseModel):
    sheet_url: str = ""
    dry_run: bool = True


@app.get("/health")
async def health():
    return {
        "status":"ok","dry_run":DRY_RUN,"mode":MODE,
        "llm_provider":LLM_PROVIDER,"search_provider":SEARCH_PROVIDER,
        "has_search":_has_search(),
    }


@app.post("/init-sheet")
async def init_sheet(request: Request):
    await _check_key(request)
    try:
        return JSONResponse(initialise_google_sheet())
    except Exception as e:
        log.exception("init-sheet failed")
        raise FastAPIHTTPException(500, str(e))

@app.post("/import-old-data")
async def import_old(request: Request):
    await _check_key(request)
    try:
        return JSONResponse(import_old_spreadsheets())
    except Exception as e:
        log.exception("import-old-data failed")
        raise FastAPIHTTPException(500, str(e))

@app.post("/init-and-import")
async def init_imp(request: Request):
    await _check_key(request)
    try:
        return JSONResponse(init_and_import())
    except Exception as e:
        log.exception("init-and-import failed")
        raise FastAPIHTTPException(500, str(e))


@app.post("/generate-ready-drafts")
async def gen_ready(req: GenReq, request: Request):
    await _check_key(request)
    try:
        sheet_url = req.sheet_url or GOOGLE_SHEET_URL
        result = generate_drafts(mode="google", sheet_url=sheet_url, force=req.force)
        return JSONResponse(result)
    except Exception as e:
        log.exception("generate-ready-drafts failed")
        raise FastAPIHTTPException(500, str(e))


@app.post("/generate-selected-draft")
async def gen_sel(req: GenReq, request: Request):
    await _check_key(request)
    if not req.row_number:
        raise FastAPIHTTPException(400, "row_number required")
    try:
        sheet_url = req.sheet_url or GOOGLE_SHEET_URL
        result = generate_drafts(
            row_number=req.row_number, mode="google",
            sheet_url=sheet_url, force=req.force,
        )
        return JSONResponse(result)
    except Exception as e:
        log.exception("generate-selected-draft failed")
        raise FastAPIHTTPException(500, str(e))


@app.post("/auto-approve")
async def approve(req: ApproveReq, request: Request):
    await _check_key(request)
    try:
        sheet_url = req.sheet_url or GOOGLE_SHEET_URL
        result = auto_approve(req.min_confidence, mode="google", sheet_url=sheet_url)
        return JSONResponse(result)
    except Exception as e:
        log.exception("auto-approve failed")
        raise FastAPIHTTPException(500, str(e))


@app.post("/send-approved")
async def send(req: SendReq, request: Request):
    await _check_key(request)
    try:
        sheet_url = req.sheet_url or GOOGLE_SHEET_URL
        result = send_approved(req.dry_run, mode="google", sheet_url=sheet_url)
        return JSONResponse(result)
    except Exception as e:
        log.exception("send-approved failed")
        raise FastAPIHTTPException(500, str(e))

# ============================================================================
# CLI ENTRY POINT
# ============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SpeakHire Outreach Worker")
    parser.add_argument("--server", action="store_true", help="Run FastAPI server")
    parser.add_argument("--init-sheet", action="store_true", help="Initialise Google Sheet with tabs + dropdowns")
    parser.add_argument("--import-old-data", action="store_true", help="Import old spreadsheets into Google Sheet")
    parser.add_argument("--init-and-import", action="store_true", help="Init sheet AND import old data")
    parser.add_argument("--row", type=int, help="Process specific row number")
    parser.add_argument("--approve", action="store_true", help="Auto-approve safe drafts")
    parser.add_argument("--send", action="store_true", help="Send approved emails")
    parser.add_argument("--force", action="store_true", help="Force re-generate even if already DRAFTED")
    parser.add_argument("--dry-run", type=lambda x: x.lower()=="true", default=None,
                        help="Override DRY_RUN (true/false)")
    args = parser.parse_args()

    if args.server:
        import uvicorn
        log.info(f"Starting server on {BACKEND_HOST}:{BACKEND_PORT}")
        uvicorn.run(app, host=BACKEND_HOST, port=BACKEND_PORT)
    elif args.init_and_import:
        result = init_and_import()
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    elif args.init_sheet:
        result = initialise_google_sheet()
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    elif args.import_old_data:
        result = import_old_spreadsheets()
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"MODE={MODE} | LLM={LLM_PROVIDER} | MODEL={MODEL_NAME} | SEARCH={SEARCH_PROVIDER} | DRY_RUN={DRY_RUN}")
        if args.send:
            result = send_approved(args.dry_run, mode=MODE)
        elif args.approve:
            result = auto_approve(mode=MODE)
        else:
            result = generate_drafts(row_number=args.row, mode=MODE, force=args.force)
        print(f"\n--- RESULT ---")
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        if MODE == "local":
            print(f"\nLocal file: {os.path.abspath(LOCAL_XLSX_PATH)}")
