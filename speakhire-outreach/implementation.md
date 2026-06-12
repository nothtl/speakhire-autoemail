# General Outreach - Line-by-Line Code Walkthrough

Full architecture: `../IMPLEMENTATION.md`. Shared modules: `../shared/implementation.md`.

## File map

```
speakhire-outreach/
├── generate.py               ← Orchestrator: reads sheet, patches worker, processes rows
├── outreach_prompt.py        ← AI instructions for sponsor/partner/individual
├── outreach_worker.py        ← Engine: 1796 lines - research, LLM, sheet, email, FastAPI
├── outreach_send.js          ← Gmail sender (Apps Script)
├── speakhire-outreach-simple/ ← .env, credentials, requirements
└── _data/                    ← Lead CSVs + prepare_leads.py
```

Unlike the simpler campaign scripts, this system uses a **worker-based architecture**. `generate.py` is an orchestrator - it reads rows from the sheet, patches the worker module with the right prompt per row, and delegates all heavy lifting to `outreach_worker.py`.

---

## `generate.py` - Line by Line

### Path setup (lines 20-30)

```python
WORKER_DIR = os.environ.get(
    "OUTREACH_WORKER_DIR",
    os.path.join(SCRIPT_DIR, "speakhire-outreach-simple"),
)
if not os.path.isdir(WORKER_DIR):
    WORKER_DIR = os.path.join(SCRIPT_DIR, "speakhire-outreach-shared")

sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, WORKER_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, '_data'))
```

**Three-way path resolution:**
1. `SCRIPT_DIR` - so `from outreach_prompt import ...` works
2. `WORKER_DIR` - so `import outreach_worker` finds the engine
3. `_data/` - so `from campaign_prompts import ...` still works (legacy import path)

The `WORKER_DIR` has a fallback: tries `speakhire-outreach-simple` first (where the `.env` and `outreach_worker.py` live), then `speakhire-outreach-shared` as a backup.

### Env loading with multi-path fallback (lines 35-41)

```python
for env_dir in ["speakhire-outreach-simple", "speakhire-outreach-shared", "."]:
    env_path = os.path.join(SCRIPT_DIR, env_dir, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break
else:
    load_dotenv()
```

**Tries 3 locations for `.env`**, then falls back to current directory. The `for...else` pattern in Python runs the `else` block only if the loop completes without `break` - meaning no `.env` was found in any of the expected locations.

### 37-column schema (lines 58-70)

```python
ROW_COLUMNS = [
    "ORG_NAME", "ORG_WEBSITE", "RECIPIENT", "EMAIL",
    "CONTACT_FIRST_NAME", "CONTACT_LAST_NAME",
    "STATUS", "CAMPAIGN_TYPE", "NOTES",
    ...
    "LAST_UPDATED",
]
```

The most complex sheet schema in the codebase. Each column is a string key used as a dict key throughout the code. The order in this list matches the sheet column order (A, B, C, ...).

```python
OUTPUT_COLUMNS = {
    "RESEARCH_QUERY", "EVIDENCE_TITLE", "EVIDENCE_SUMMARY", ...
    "STATUS", "ERROR",
}
```

`OUTPUT_COLUMNS` is a subset - only the columns that `generate_for_row()` writes to. The other columns (like `ORG_NAME`, `EMAIL`) are input-only and never overwritten.

### `read_rows()` (lines 105-128)

```python
data = ws.get_all_records(expected_headers=ROW_COLUMNS)
```

**`expected_headers` guarantees column order.** Without it, `get_all_records()` returns columns in whatever order the sheet has them, which might differ from `ROW_COLUMNS`. Passing `expected_headers` maps the sheet's columns to the expected order.

```python
for i, rd in enumerate(data):
    r = {}
    for c in ROW_COLUMNS:
        val = rd.get(c, "")
        if val is None or (isinstance(val, float) and str(val) == "nan"):
            val = ""
        r[c] = str(val).strip()
```

**Manual NaN handling instead of `safe_str()`** because this script predates the shared module. Same logic: None → "", NaN float → "", everything else → stripped string.

```python
if not r.get("RECIPIENT", ""):
    first = r.get("CONTACT_FIRST_NAME", "")
    last = r.get("CONTACT_LAST_NAME", "")
    if first and last:
        r["RECIPIENT"] = f"{first} {last}"
```

**Derives RECIPIENT from first/last name** if the RECIPIENT column is blank. This handles cases where leads are imported with separate name columns but the combined field wasn't populated.

```python
r["_row"] = i + 2  # row 1 = header
```

**The `_row` key** stores the actual sheet row number. It's prefixed with underscore to indicate it's metadata (not a sheet column). Used by `write_rows()` to know where to write back.

### `generate_for_row()` - Runtime prompt patching (lines 150-231)

```python
campaign = row.get("CAMPAIGN_TYPE", "").strip().lower()
if campaign not in CAMPAIGN_TYPES:
    return {"STATUS": "ERROR", "ERROR": f"Invalid CAMPAIGN_TYPE: '{campaign}'"}
```

Validates the campaign type dropdown. The sheet has data validation on this column, but manual edits can bypass it. This check catches bad values before they reach the worker.

```python
# Patch worker with campaign-specific prompt + sender
worker.SYSTEM_PROMPT = get_prompt(campaign)
sender = get_sender(campaign)
worker.DEFAULT_SENDER_NAME = sender["name"]
worker.DEFAULT_SENDER_ORG = sender["org"]

_orig_get_sender_title = worker.get_sender_title
worker.get_sender_title = lambda r: worker.clean(r.get("SENDER_TITLE")) or sender["title"]
```

**This is the key architectural pattern.** Instead of passing the prompt as a parameter, `generate.py` directly patches module-level variables on the worker. The worker's functions (`research()`, `generate_draft()`) read `worker.SYSTEM_PROMPT` from the module scope. This means:
- The worker is stateless between calls
- Each row can use a different prompt without restarting
- No function signature changes needed in the worker

**The sender title lambda** is saved and restored after each row because the worker might have its own sender title logic for other use cases.

```python
if campaign == "individual" and not org_website:
    search_query = "(individual invite — no web research)"
    search_results = [{"title": "Profile", "snippet": f"Profile notes: {notes}\nSegment: {segment}", "url": ""}]
    website_text = ""
else:
    search_query, search_results, website_text = worker.research(org_name, org_website)
```

**Individual campaign skip:** Individual invites don't need website research - the personalization comes from profile data. The script creates a dummy search result with the contact's notes and segment so the LLM prompt builder has something to work with.

```python
try:
    draft = worker.generate_draft(...)
except Exception as e:
    draft = worker._fallback_draft(org_name, recipient, has_email,
                                   sender_name, sender_org, sender_title, str(e))
```

**Three-tier fallback inside `worker.generate_draft()`** (see worker docs), plus an outer fallback to `_fallback_draft()` if even that fails. The fallback produces a simple template email so a single failure doesn't block the batch.

```python
worker.get_sender_title = _orig_get_sender_title
```

**Restores the original function** after each row. This is critical - if a row patches `get_sender_title` to a lambda and the next row doesn't use `get_sender`, the stale lambda would persist.

```python
field_map = {
    "RESEARCH_QUERY": "research_query",
    "EVIDENCE_TITLE": "evidence_title",
    ...
}
out = {}
for col, key in field_map.items():
    out[col] = draft.get(key, "")
```

**Case normalization:** The LLM returns lowercase keys (`email_subject`). The sheet columns are UPPERCASE (`EMAIL_SUBJECT`). This mapping bridges the two conventions.

### Main loop (lines 238-296)

```python
for i, row in enumerate(rows):
    status = row.get("STATUS", "").upper()

    if status != "READY_FOR_RESEARCH":
        continue

    if row.get("OPT_OUT", "").upper() in ("TRUE", "YES", "1"):
        continue
```

**Two skip conditions:** Only processes `READY_FOR_RESEARCH` rows. Skips `OPT_OUT` rows (people who asked not to be contacted). The status check means this script is idempotent - running it twice only processes new rows.

```python
output = generate_for_row(row)

for col, val in output.items():
    if col in ROW_COLUMNS:
        row[col] = val

changed.append(i)
```

**In-place mutation:** The row dict is updated directly. `write_rows()` later writes back all changed rows in one batch. The `col in ROW_COLUMNS` guard prevents writing keys that aren't sheet columns.

---

## `outreach_prompt.py` - Three Campaign Types

### CAMPAIGN_SENDERS (lines 13-17)
```python
CAMPAIGN_SENDERS = {
    "sponsor":    {"name": "Hana", "title": "Partnerships Lead, SpeakHire"},
    "partner":    {"name": "Hana", "title": "Campaign Coordinator, #SpeakingMyName"},
    "individual": {"name": "Hana", "title": "Community Engagement, SpeakHire"},
}
```

Hana has three different titles depending on who she's emailing. The worker patches `DEFAULT_SENDER_NAME` and `get_sender_title` per row so the email signature matches the campaign type.

### CAMPAIGN_META (lines 22-26)
```python
CAMPAIGN_META = {
    "sponsor":    {"label": "Soiree Sponsorship", "target_has_org": True},
    "partner":    {"label": "#SpeakingMyName Partner", "target_has_org": True},
    "individual": {"label": "Soiree Invitation", "target_has_org": False},
}
```

`target_has_org` controls whether website research runs. Sponsors and partners need org websites scraped; individuals use profile data only.

### JSON output format difference

Unlike the simpler campaign scripts (which return `{"email_subject": ..., "email_body": ...}`), these prompts return 11-field evidence-tracking JSON:

```json
{
  "evidence_title": "Grow with Google initiative",
  "evidence_summary": "Referenced Google's digital skills program...",
  "source_url": "https://...",
  "evidence_confidence": "HIGH",
  "personalised_opener": "Your Grow with Google initiative has...",
  "email_subject": "...",
  "email_draft": "...",
  "review_status": "NEEDS_REVIEW",
  "error": ""
}
```

This is why the sheet has EVIDENCE_* columns - the general outreach system tracks not just the email, but what research was used to personalize it and how confident the AI is about that evidence. The simpler campaigns skip this because their personalization is more straightforward.

## `outreach_worker.py` - Engine (summary)

At 1796 lines, the worker is the largest file in the codebase. Key functions:

| Function | Lines | What it does |
|---|---|---|
| `research()` | ~80 | Website crawl (homepage + 5 scored subpages) + optional search API |
| `generate_draft()` | ~120 | Three-tier LLM: LangChain structured → direct HTTP → fallback template |
| `validate_evidence()` | ~30 | Downgrades HIGH→MEDIUM if source URL not in research |
| `_send()` | ~80 | Sends via Gmail SMTP or SendGrid, honors DRY_RUN |
| FastAPI app | ~200 | `/generate`, `/approve`, `/send`, `/init-sheet` endpoints |
| `read_rows()` / `write_rows()` | ~100 | 37-column sheet I/O with header normalization |
| Import/init helpers | ~200 | Dropdown creation, conditional formatting, old spreadsheet migration |

The worker predates the `shared/` modules. It duplicates some functionality (`clean()`, sheet connection, env loading) that now also exists in `shared/generator.py`. Over time, the worker could be refactored to use shared functions.
