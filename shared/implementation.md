# Shared Modules - Line-by-Line Code Walkthrough

These two files are the engine every campaign script runs on. Change something here and every campaign picks it up. Keep them clean and tested.

## `config.py` - All configuration in one place

### Paths (lines 11–19)

```python
SHARED_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR   = os.path.dirname(SHARED_DIR)
ENV_DIR    = os.path.join(ROOT_DIR, 'speakhire-outreach', 'speakhire-outreach-simple')
```

`__file__` is the path to `config.py` itself. `os.path.abspath` resolves any symlinks or relative references. From there we walk up twice: `shared/` → `autoemail/` (ROOT_DIR). The `.env` file lives in `autoemail/speakhire-outreach/speakhire-outreach-simple/.env`.

**Why relative paths:** No hardcoded `C:\Users\...` paths. If you move the project, it still works as long as `shared/` stays at the repo root.

### Env loading (lines 25–26)

```python
from dotenv import load_dotenv
load_dotenv(os.path.join(ENV_DIR, '.env'))
```

`python-dotenv` reads a `.env` file and injects its key=value pairs into `os.environ`. After this call, `os.getenv('GOOGLE_SHEET_URL')` returns the value from the file. The import is inside the function because some scripts import config before dotenv is on their path.

### LLM selection (lines 32–40)

```python
OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY', '')
if OPENROUTER_KEY:
    API_KEY   = OPENROUTER_KEY
    BASE_URL  = 'https://openrouter.ai/api/v1'
    LLM_MODEL = 'google/gemma-4-31b-it:free'
else:
    API_KEY   = os.getenv('DEEPSEEK_API_KEY')
    BASE_URL  = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
    LLM_MODEL = 'deepseek-chat'
```

**OpenRouter first, DeepSeek fallback.** The Gemma model on OpenRouter has a free tier - testing with `--rows 3` costs $0. If `OPENROUTER_API_KEY` is set in `.env`, it uses OpenRouter. Otherwise it falls back to DeepSeek (paid).

**To swap models:** Change `LLM_MODEL` here. All campaigns use `from shared.config import LLM_MODEL`, so one edit changes the model everywhere. Example: `LLM_MODEL = 'deepseek-chat'` even when OpenRouter is configured.

### Sheet config (lines 46–47)

```python
SHEET_URL  = os.getenv('GOOGLE_SHEET_URL')
CREDS_PATH = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
```

`SHEET_URL` is the full Google Sheets URL (e.g. `https://docs.google.com/spreadsheets/d/abc123/edit`). `CREDS_PATH` points to the Google Cloud service account JSON key file.

### Bot headers (line 53)

```python
BOT_HEADERS = {"User-Agent": "Mozilla/5.0 (SpeakHire Outreach Bot; nonprofit use)"}
```

Identifies our scraper to websites. Good practice for nonprofit bots - some sites block default Python `requests` user agents.

---

## `generator.py` - Common engine

### Module-level imports (lines 13–18)

```python
import argparse, io, json, os, re, sys, time

from shared.config import (
    API_KEY, BASE_URL, LLM_MODEL, OPENROUTER_KEY,
    SHEET_URL, CREDS_PATH, BOT_HEADERS,
)
```

Imports the config values at module level so every function can use them without passing parameters. Campaign scripts that need these values import them from config directly; generator imports them here for its own use.

---

### `fix_windows_encoding()` (lines 24–30)

```python
def fix_windows_encoding():
    if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding != 'utf-8':
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        except (ValueError, AttributeError):
            pass
```

**Why this exists:** Windows terminals default to cp1252 or similar legacy encodings. When the LLM generates an email with Chinese, Arabic, or accented characters, `print()` crashes with `UnicodeEncodeError`. This function wraps stdout in a UTF-8 text wrapper that replaces unprintable characters instead of crashing.

**The guard clause** (`if not isinstance...`) means it only wraps when needed. On macOS/Linux or when already UTF-8, it's a no-op.

**Every `generate_*.py` calls this as its first action** after imports:
```python
fix_windows_encoding()
```

---

### `parse_args()` (lines 37–57)

```python
def parse_args(description="Generate outreach emails", extra_args=None):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--rows', type=int, help='Generate only first N rows')
    parser.add_argument('--row', type=int, help='Generate only a specific row')
    parser.add_argument('--preview', action='store_true', help='Preview contacts without calling LLM')
    parser.add_argument('--force', action='store_true', help='Regenerate even if already drafted')
    if extra_args:
        for ea in extra_args:
            kwargs = dict(ea)
            name = kwargs.pop('name')
            parser.add_argument(name, **kwargs)
    return parser.parse_args()
```

**Standard flags every campaign gets:**
| Flag | What it does |
|---|---|
| `--rows N` | Process only first N contacts. Use 1-3 for testing. |
| `--row N` | Process only a specific row number (in the source data, not the sheet). |
| `--preview` | Print what would happen, then exit. Zero API calls. |
| `--force` | Ignore "already drafted" checks and regenerate. |

**Extra args** let each campaign add its own flags. Example from Soirée:
```python
args = parse_args("Generate Soiree emails", extra_args=[
    {"name": "--csv", "required": True, "help": "CSV file of contacts"},
    {"name": "--type", "required": True, "choices": ["sponsor", "individual"]},
])
```

**The `kwargs.pop('name')` pattern:** argparse requires the flag name as the first positional argument, not a keyword. By storing it under `"name"` in the dict and popping it, the caller can use a clean dict syntax for everything else.

---

### `call_llm()` (lines 64–119)

This is the most important function in the codebase - every email goes through it.

#### Headers (lines 68–74)
```python
headers = {
    'Authorization': f'Bearer {API_KEY}',
    'Content-Type': 'application/json',
}
if OPENROUTER_KEY:
    headers['HTTP-Referer'] = 'https://speakhire.org'
    headers['X-Title'] = 'SpeakHire Outreach'
```
OpenRouter requires `HTTP-Referer` and `X-Title` headers to identify the app. Without them, requests are rejected with 403. DeepSeek ignores extra headers, so it's safe to always include them when an OpenRouter key is configured.

#### Retry loop (lines 76–96)
```python
for attempt in range(max_retries):
    resp = requests.post(
        f'{BASE_URL}/chat/completions',
        headers=headers,
        json={
            'model': LLM_MODEL,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'temperature': 0.7,
        },
        timeout=90,
    )
    if resp.status_code == 429:
        wait = (2 ** attempt) * 3  # 3s, 6s, 12s
        print(f'  Rate limited, waiting {wait}s...')
        time.sleep(wait)
        continue
    resp.raise_for_status()
    break
```

**The POST:** Sends a standard OpenAI-compatible chat completion request. `temperature=0.7` gives the LLM some creative flexibility while staying mostly deterministic. `timeout=90` means we wait up to 90 seconds for a response (long emails or slow models).

**Rate limit handling:** On HTTP 429 (too many requests), the function waits with exponential backoff: 3 seconds, then 6, then 12. After 3 failures it lets the exception propagate. This is rarely hit in practice because campaign scripts sleep 0.5s between calls.

**The `messages` array** uses the standard system/user pattern:
- `system` - tells the AI who it is, how to write, what tone to use (the prompt from `*_prompt.py`)
- `user` - gives specific info about THIS recipient (name, org, research findings)

#### Response parsing (lines 98–119)
```python
content = resp.json()['choices'][0]['message']['content'].strip()

# Clean markdown wrappers
if content.startswith('```'):
    content = content[content.find('\n'):].strip()
if content.endswith('```'):
    content = content[:-3].strip()

# Extract JSON
s, e = content.find('{'), content.rfind('}')
if s != -1 and e != -1:
    content = content[s:e+1]

# Sanitize invisible characters
content = ''.join(c for c in content if ord(c) >= 32 or c in '\n\r\t')

try:
    return json.loads(content)
except json.JSONDecodeError:
    # Fallback: ASCII-only
    content = ''.join(c for c in content if 32 <= ord(c) <= 126 or c in '\n\r')
    return json.loads(content)
```

**Step by step:**

1. **Extract the message:** `resp.json()['choices'][0]['message']['content']` - the standard path in OpenAI-compatible responses. `.strip()` removes leading/trailing whitespace.

2. **Strip markdown fences:** LLMs often wrap JSON in ` ```json ... ``` `. The code strips the opening fence (everything before the first newline) and closing fence (last 3 backticks).

3. **Extract JSON bounds:** Finds the outermost `{...}` pair. This handles cases where the LLM adds explanatory text before or after the JSON.

4. **Sanitize invisible chars:** Keeps only printable Unicode (codepoint ≥ 32) plus newlines and tabs. This removes zero-width spaces, bidirectional markers, and other invisible characters that break `json.loads()`.

5. **First parse attempt:** `json.loads()` on the cleaned content. Works ~95% of the time.

6. **ASCII fallback:** If parsing fails, strips everything except printable ASCII (32–126). This handles rare cases where the LLM includes curly quotes or other Unicode that survived the first sanitize pass. The second `json.loads()` will raise if this also fails - the caller must handle it.

---

### `safe_str()` (lines 126–132)

```python
def safe_str(val):
    if val is None:
        return ''
    if isinstance(val, float):
        return '' if str(val) == 'nan' else str(val)
    return str(val).strip()
```

**Why this is needed:** Google Sheets and Excel both return `NaN` for empty numeric cells. `str(float('nan'))` produces the string `"nan"`, which would appear in email greetings as "Hi nan,". This function converts NaN to empty string.

**The three cases:**
- `None` → `''` (empty cells from gspread)
- `float('nan')` → `''` (empty numeric cells)
- Everything else → `str(val).strip()` (normal values, with whitespace trimmed)

**Used everywhere:** Every script that reads from sheets or xlsx calls `safe_str()` on every cell value.

---

### `clean_email()` (lines 135–146)

```python
def clean_email(text):
    if not text:
        return ''
    text = text.replace('-', '-').replace('–', '-')   # em/en dashes
    text = text.replace('‘', "'").replace('’', "'")   # smart quotes
    text = text.replace('“', '"').replace('”', '"')
    text = text.replace('…', '...')                        # ellipsis
    text = re.sub(r', right\?', '?', text)                      # fake-casual tics
    text = re.sub(r'\bright\?\b', '', text)
    text = re.sub(r', you know\?', '?', text)
    return text.strip()
```

**What it cleans and why:**

| Artifact | Example | Why it's a problem |
|---|---|---|
| Em/en dashes | `-` `–` | Gmail renders them as `â€"` in plain text mode |
| Smart quotes | `'` `'` `"` `"` | Same encoding issue; some email clients mangle them |
| Ellipsis | `…` | Same as above |
| "right?" | `you speak French, right?` | LLM fake-casual tic - sounds inauthentic |
| "you know?" | `it's important, you know?` | Same - reads as AI-generated |

**The regex patterns:**
- `r', right\?'` - matches `, right?` (the comma before it is key - avoids matching the word "right" in other contexts)
- `r'\bright\?\b'` - matches standalone `right?` with word boundaries
- `r', you know\?'` - matches `, you know?`

**Each campaign script calls `clean_email()` on both the subject and body** before writing to the sheet.

---

### `get_sheet_id()` (lines 153–156)

```python
def get_sheet_id():
    m = re.search(r'/d/([a-zA-Z0-9\-_]+)', SHEET_URL)
    return m.group(1) if m else None
```

Extracts the Google Sheet ID from a URL like `https://docs.google.com/spreadsheets/d/abc123/edit`. The regex matches the path segment after `/d/` - Google Sheet IDs are alphanumeric with dashes and underscores.

---

### `connect_sheet()` (lines 159–183)

```python
def connect_sheet(tab_name, headers=None):
    import gspread

    gc = gspread.service_account(filename=CREDS_PATH)
    sh = gc.open_by_key(get_sheet_id())

    try:
        return sh.worksheet(tab_name)
    except Exception:
        if headers is None:
            headers = [
                'Contact Name', 'Title', 'Organization', 'Email',
                'Campaign Type', 'Status', 'Notes',
                'Email Subject', 'Personalized Email',
                'Research Notes', 'Sent At', 'Extra',
            ]
        num_cols = len(headers)
        ws = sh.add_worksheet(title=tab_name, rows='500', cols=str(num_cols))
        ws.update(values=[headers], range_name='A1')
        last_col = chr(64 + num_cols) if num_cols <= 26 else 'Z'
        ws.format(f'A1:{last_col}1', {'textFormat': {'bold': True}})
        ws.freeze(rows=1)
        return ws
```

**What happens:**

1. **Authenticate:** `gspread.service_account()` reads the JSON key file. This is a Google Cloud service account - it acts as a robot user that has been granted access to the Google Sheet.

2. **Open the sheet:** `gc.open_by_key()` uses the extracted sheet ID.

3. **Get or create the tab:** `sh.worksheet(tab_name)` tries to open an existing tab. If it doesn't exist, `gspread` raises an exception, which triggers the creation path.

4. **Create the tab:** `sh.add_worksheet()` creates a new tab with 500 rows and `num_cols` columns. The headers are written to row 1, bolded, and the row is frozen (stays visible when scrolling).

**The `chr(64 + num_cols)` trick:** Converts column count to letter. 1→A, 12→L, 26→Z. For >26 columns (like the Outreach Tracker's 37), it falls back to `'Z'` and the format range covers less, which is fine since the extra columns are rarely formatted.

**Default headers** are the 12-column schema used by Soirée, SMN, and Summit. The speakhire-outreach system uses its own 37-column schema and passes custom headers.

---

### `sheet_link()` (lines 186–189)

```python
def sheet_link():
    return f'https://docs.google.com/spreadsheets/d/{get_sheet_id()}/edit'
```

One-liner convenience for printing clickable sheet URLs at the end of a run. Note: this doesn't include the `gid` parameter (tab ID), so it always opens the first tab. The campaign scripts print a more specific URL using `ws.id` after connecting.

---

## How a campaign script uses these modules

Here's the complete import pattern every `generate_*.py` follows:

```python
import os, sys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..'))    # add repo root to path

from shared.config import *     # API_KEY, LLM_MODEL, SHEET_URL, BOT_HEADERS, etc.
from shared.generator import (  # call_llm, clean_email, safe_str, connect_sheet,
    fix_windows_encoding,       #   parse_args, get_sheet_id
    parse_args, call_llm,
    clean_email, safe_str,
    connect_sheet, get_sheet_id,
)
from *_prompt import get_prompt, CAMPAIGN

fix_windows_encoding()  # must be called before any print() with non-Latin text
```

After these imports, the campaign script only needs to define:
1. **Data reader** - `read_contacts()` or similar
2. **Research** - `research_org()` / `research_person()` or skip
3. **Prompt builder** - `build_user_prompt()` or inline in main()
4. **Main loop** - iterate contacts, call `call_llm()`, write to sheet

Everything else (LLM calling, text cleaning, sheet connection, CLI) is handled by these two files.
