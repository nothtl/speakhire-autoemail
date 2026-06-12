# Soiree Outreach - Line-by-Line Code Walkthrough

Full architecture: `../IMPLEMENTATION.md`. Shared modules: `../shared/README.md`.

## File map

```
soiree_outreach/
├── generate_soiree_emails.py   ← one script, three campaign types
├── soiree_prompt.py            ← AI instructions + event facts
├── soiree_send.js              ← Gmail sender (Apps Script)
└── _data/                      ← Network of Influence.xlsx
```

---

## `generate_soiree_emails.py` - Line by Line

### Imports (lines 1-37)

```python
import csv, os, re, sys, time
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, SCRIPT_DIR)
```

**Two `sys.path` insertions:**
1. `'..'` (repo root) - so `from shared.config import ...` finds the shared module. Every campaign script needs this.
2. `SCRIPT_DIR` (soiree_outreach/) - so `from soiree_prompt import ...` works. Python needs the current directory on the path when importing sibling modules.

```python
from shared.config import BOT_HEADERS, LLM_MODEL, BASE_URL
from shared.generator import (
    fix_windows_encoding, parse_args, call_llm,
    clean_email, safe_str, connect_sheet, get_sheet_id,
)
from soiree_prompt import get_prompt, CAMPAIGN, SOIREE_DATE, SOIREE_TIME, SOIREE_VENUE, SOIREE_TICKET
```

**Three import sources:**
- `shared.config` - API keys, model, bot headers
- `shared.generator` - the common engine (LLM caller, helpers, sheet, CLI)
- `soiree_prompt` - campaign-specific: AI instructions, event facts, `CAMPAIGN` dict

The Soiree event constants (`SOIREE_DATE`, etc.) are imported directly because the `hetal_people` prompt builder uses them to construct the user prompt.

```python
fix_windows_encoding()
import requests, openpyxl
```

`fix_windows_encoding()` must be called before any `print()` with non-Latin text. `requests` (HTTP) and `openpyxl` (Excel) are imported after because they don't need the path setup.

### Config (lines 42-44)

```python
PEOPLE_XLSX_PATH = os.path.join(SCRIPT_DIR, '_data', 'Network of Influence.xlsx')
PEOPLE_XLSX_TAB  = 'People'
PEOPLE_START_ROW = 4
PEOPLE_END_ROW   = 129
```

The xlsx path is relative to the script directory. Rows 4-129 in the People tab contain Hetal's network contacts. Rows 1-3 are header/metadata rows.

### CSV Reader (lines 50-90) - `read_csv()`

```python
name = row.get('name', row.get('full name', row.get('Name', row.get('Full Name',
       row.get('first name', row.get('First Name', '')))))).strip()
```

**Cascading `.get()` pattern:** Tries 6 column name variants for "name". If the first returns `None`, the next is tried. This handles CSVs from different sources (exported from Google Sheets, Excel, Salesforce, etc.) without requiring column name normalization.

```python
first = row.get('first name', row.get('First Name', row.get('first_name', ''))).strip()
last = row.get('last name', row.get('Last Name', row.get('last_name', ''))).strip()
if first and last:
    name = f"{first} {last}"
```

Separate first/last name detection. Some CSVs split names into two columns instead of one. The script handles both formats.

```python
email_cols = ['email', 'Email', 'EMAIL', 'contact email', 'Contact Email']
email = next((row.get(c, '').strip() for c in email_cols if row.get(c, '').strip()), '')
```

**Generator + `next()` pattern:** Creates a generator that yields the first non-empty email column match. `next()` with a default of `''` means it returns empty string if no column matches. More concise than cascading `.get()` for the email case.

The same pattern is used for org, title, interests, and career field columns. Each has its own list of common column name variants.

```python
if not name and not org:
    continue
```

Skips completely empty rows. A row needs at least a name OR an organization to be useful.

### XLSX Reader (lines 96-148) - `read_people_from_xlsx()`

```python
wb = openpyxl.load_workbook(PEOPLE_XLSX_PATH)
ws = wb[PEOPLE_XLSX_TAB]
```

Opens the Excel file and selects the "People" tab. `openpyxl` reads `.xlsx` files without requiring Excel installed.

```python
for row_idx, row in enumerate(ws.iter_rows(min_row=START_ROW, max_row=END_ROW, values_only=True), START_ROW):
    vals = [safe_str(v) for v in row]
```

`iter_rows(values_only=True)` returns cell values directly (not Cell objects). `safe_str()` is called on every cell immediately to normalize NaN/None to empty strings. The enumerate starts at `START_ROW` (4) so `row_idx` matches the actual Excel row number.

```python
full_name = vals[2] if len(vals) > 2 else ''
company   = vals[3] if len(vals) > 3 else ''
email     = vals[4] if len(vals) > 4 else ''
```

Direct column index access. The People tab has a fixed column order: col A=status, B=tags, C=full_name, D=company, E=email, etc. The `len(vals) > N` guard handles rows with fewer columns than expected (e.g., trailing empty rows).

```python
if ',' in full_name and not full_name.startswith('http'):
    parts = [p.strip() for p in full_name.split(',', 1)]
    if len(parts) == 2 and parts[0] and parts[1]:
        last = parts[0].strip()
        first = re.sub(r'\s*\(.*?\)\s*', '', parts[1]).strip()
        first = re.sub(r'\s*<.*', '', first).strip()
        if first and last:
            full_name = f"{first} {last}"
```

**"LastName, FirstName" fix:** Some entries are formatted as "Cehonski, Irak (QueensBP)" or "Smith, John <email>". The regex `\s*\(.*?\)\s*` strips parenthetical suffixes like "(QueensBP)". The regex `\s*<.*` strips angle-bracket suffixes like "<email>". After cleaning, it reassembles as "FirstName LastName".

```python
name_parts = full_name.split()
first_name = name_parts[0] if name_parts else ''
last_name = name_parts[-1] if len(name_parts) > 1 else ''
if last_name.lower() in ('jr', 'sr', 'ii', 'iii', 'ed.d'):
    last_name = name_parts[-2] if len(name_parts) > 2 else ''
```

Extracts first/last name from the cleaned full name. The suffix check prevents "Jr." or "III" from being treated as the last name.

```python
if not email or '@' not in email:
    continue
has_donated = bool(amount and amount != '0' and amount != '0.0')
```

Only includes contacts with valid email addresses. `has_donated` is True only if the amount column has a non-zero value - used later to tailor the email tone for past donors.

### Shared Research Utilities (lines 154-182)

```python
def fetch_url_text(url, timeout=10):
    result = {"url": url, "title": "", "text": "", "error": ""}
```

**Never-crash pattern:** Every function that makes HTTP requests returns a dict with an `error` field. Callers check `result["error"]` before using the data. This means a single failed request doesn't crash the whole batch.

```python
for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
    tag.decompose()
```

`decompose()` removes the tag and its contents from the parse tree. This strips navigation, footers, scripts, and styles before extracting text, leaving only content-relevant text. The result is trimmed to 5000 characters to keep LLM prompts under context limits.

```python
def search_org_website(org_name, email_hint=""):
    # Strategy 0: Extract from email
    if email_hint and '@' in email_hint:
        email_domain = email_hint.split('@')[-1].strip().lower()
        skip = ['gmail.com', 'yahoo.com', 'hotmail.com', ...]
        if email_domain not in skip:
```

**Two strategies, fast-fail:** Strategy 0 extracts the domain from the contact's email (e.g., `john@acmecorp.com` → try `acmecorp.com`). Personal email domains (gmail, yahoo, etc.) are skipped since they don't represent an organization's website.

```python
    # Strategy 1: Domain patterns
    clean = re.sub(r'[^a-z0-9\s]', '', org_name.lower().strip())
    clean = re.sub(r'\s+', '', clean)
    for url in [f"https://www.{clean}.org", f"https://{clean}.org", f"https://www.{clean}.com"]:
```

Strategy 1 guesses URLs from the org name: "Acme Corp" → `acmecorp.org`, `acmecorp.com`. Only 3 attempts - no Bing fallback (unlike the SMN script which has deeper research). If all 3 fail, the LLM works from the org name alone.

### Sponsor Research (lines 188-212) - `research_org()`

```python
keywords = ["mission", "program", "community", "serve", "diversity", "inclusion",
            "equity", "belonging", "impact", "support", "partner", "initiative",
            "csr", "philanthropy", "grant", "workforce", "education", "youth"]
sentences = [s.strip() for s in text.replace('\n', '. ').split('.') if len(s.strip()) > 30]
matches = []
for s in sentences:
    if any(kw in s.lower() for kw in keywords[:6]) and len(s) > 40:
        matches.append(s[:250])
```

**Keyword-based sentence extraction:** Splits the page text on periods, filters for sentences longer than 30 chars, then keeps sentences containing any of the first 6 keywords. The result is trimmed to 250 chars per sentence. The first 5 matches become the research notes written to the sheet.

### Person Research (lines 218-267) - `research_person()`

```python
def _extract_profile_sentences(text):
    keywords = ["mission", "program", "community", "serve", "education", "youth",
                "student", "school", "leadership", "diversity", "equity", "inclusion",
                "support", "impact", "nonprofit", "philanthropy", ...]
    sentences = [s.strip() for s in text.replace('\n', '. ').split('.') if len(s.strip()) > 30]
    matches = [s[:250] for s in sentences if any(kw in s.lower() for kw in keywords[:8])]
    seen, unique = set(), []
    for m in matches:
        prefix = m[:40].lower()
        if prefix not in seen:
            seen.add(prefix)
            unique.append(m)
    return unique[:6]
```

**Deduplication by prefix:** Two sentences starting with the same 40 characters are considered duplicates. This prevents the same mission statement (repeated across multiple pages) from appearing multiple times in the research notes.

```python
def research_person(person):
    # 1. Personal/work website
    website_url = person.get('website', '')
    if website_url:
        page = fetch_url_text(website_url)
        if not page["error"] and page["text"]:
            key_info = _extract_profile_sentences(page["text"])

    # 2. Company + title context
    if company and title:
        notes_parts.insert(0, f"Role: {title} at {company}")

    # 3. Contact type, 4. Donation history, 5. Notes, 6. Description, 7. Status
```

**Layered research:** Builds context from up to 7 sources. The `notes_parts.insert(0, ...)` for role means it appears first (most important info at the top). Each layer is optional - a contact with no website and no notes still gets basic personalization from their title and company.

### Email Generation (lines 273-357) - `build_user_prompt()`

```python
def build_user_prompt(contact, campaign_type, research_text, website_url=""):
    name = contact.get('name', '') or contact.get('full_name', '') or 'there'
```

**Three-way name fallback:** CSV contacts use `name`, xlsx contacts use `full_name`. If neither exists, defaults to "there" (for edge cases).

#### Sponsor prompt (lines 284-299)
```python
research_block = f"""
COMPANY RESEARCH:
{research_text if research_text else '(No website content found...)'}
Website: {website_url or 'Not found'}"""
```

The research block is only included for sponsors. If no website was found, the LLM gets a note telling it to work from the company name and industry knowledge.

#### Individual prompt (lines 301-324)
```python
profile = []
if title: profile.append(f"Job: {title}")
if org: profile.append(f"Company: {org}")
if career_field: profile.append(f"Career field: {career_field}")
if languages: profile.append(f"Languages: {languages}")
if interests: profile.append(f"Interests: {interests}")
profile_text = '\n'.join(profile) if profile else '(minimal profile - write a warm, brief invitation)'
```

**Conditional profile building:** Each field is only included if it has a value. The fallback message tells the LLM to write a shorter, event-focused invitation when there's minimal profile data.

#### Hetal people prompt (lines 326-357)
```python
donor_block = ""
if has_donated:
    donor_block = f"""
PAST SUPPORTER: This person has donated ${amount} to SpeakHire in the past.
Acknowledge this support warmly but don't mention the specific amount."""
```

Past donors get special treatment. The amount is passed so the LLM knows the level of support, but the prompt explicitly says not to mention the specific dollar figure in the email (it's gauche).

### Main (lines 363-450)

```python
args = parse_args(
    "Generate Soiree outreach emails",
    extra_args=[
        {"name": "--csv", "help": "CSV file of contacts (sponsor/individual)"},
        {"name": "--type", "required": True,
         "choices": ["sponsor", "individual", "hetal_people"],
         "help": "Campaign type"},
    ]
)
```

**`--csv` is optional** because `hetal_people` mode reads from the hardcoded xlsx path. For sponsor/individual, `--csv` is validated in the code below (not by argparse) since making it conditionally required is complex.

```python
if args.type == 'hetal_people':
    contacts = read_people_from_xlsx()
else:
    if not args.csv:
        print("ERROR: --csv is required for sponsor/individual types")
        sys.exit(1)
    contacts = read_csv(args.csv)
```

Manual validation for the CSV requirement. The script exits with a clear error message if `--csv` is missing for sponsor/individual types.

```python
# Research
if args.type == 'sponsor' and contact.get('org'):
    website_url, research_text, research_notes = research_org(contact['org'], contact.get('email', ''))
elif args.type == 'hetal_people':
    research_text, research_notes = research_person(contact)
```

**Research is type-gated:** Sponsor mode scrapes the company website. Individual mode does no research (uses CSV profile data). Hetal people mode scrapes the person's website and assembles profile context. Each path produces `research_text` (for the LLM prompt) and `research_notes` (for the sheet).

---

## `soiree_prompt.py` - AI Instructions

### CAMPAIGN dict (lines 10-13)
```python
CAMPAIGN = {
    "name": "SpeakHire Soiree",
    "sheet_tab": "Soiree Outreach",
}
```

Used by `generate_soiree_emails.py` to know which sheet tab to write to. Every prompt file has this dict.

### Event facts (lines 19-35)
```python
SOIREE_DATE      = "Wednesday, June 24th, 2026"
SOIREE_TIME      = "5:30 PM - 9:00 PM EDT"
SOIREE_VENUE     = "Salesforce Tower, Ohana Floor (41F), 1095 6th Ave, New York"
SOIREE_TICKET    = "https://www.zeffy.com/en-US/ticketing/speakhire-soiree"
```

**Update these for each new event.** The constants are used in two places: interpolated into system prompts (for the AI) and referenced directly in `build_user_prompt()` (for the user-facing prompt). Changing them here updates everything.

### SPONSOR_SYSTEM_PROMPT (~60 lines)
The AI is told it's writing as Hana, Partnerships Lead. Key rules:
- **Personalization rule #1:** MUST name a specific program/initiative from the company's website
- **Tone:** Under 200 words, warm but professional, CTA is a 15-20 minute call
- **Banned phrases:** "your commitment to diversity," "we admire your mission"
- **JSON output:** 11 fields including evidence tracking and review status

### INDIVIDUAL_SYSTEM_PROMPT (~50 lines)
Same structure but for personal invites. Hana is "Community Engagement" here (different title than sponsor emails). Under 150 words. Profile-based personalization with examples per career type (healthcare, tech, student, multilingual).

### HETAL_PEOPLE_PROMPT (~120 lines)
Hetal Jani writing to her personal network. The longest and most detailed prompt because it handles multiple relationship types:
- **Past donors:** acknowledged warmly but proportionally
- **Event contacts:** "It was good meeting you at [event]"
- **Professional colleagues:** casual, peer-to-peer
- **Minimal info contacts:** shorter, sincere invitation

**Banned corporate jargon:** "circle back," "touch base," "move the needle," "synergize," "leverage," "bandwidth," "deep dive" - all explicitly listed as never-use.

### `get_prompt()` dispatcher
```python
def get_prompt(campaign_type: str) -> str:
    if campaign_type == "sponsor": return SPONSOR_SYSTEM_PROMPT
    elif campaign_type == "individual": return INDIVIDUAL_SYSTEM_PROMPT
    elif campaign_type == "hetal_people": return HETAL_PEOPLE_PROMPT
    else: raise ValueError(...)
```

Simple dispatcher. The ValueError intentionally crashes the script if you typo the `--type` flag, giving immediate feedback.
