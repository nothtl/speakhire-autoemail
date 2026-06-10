"""
prepare_leads.py — Convert SpeakHire CSV contact lists into outreach tracker format.

Reads champions.csv and interns.csv, categorises each contact into campaigns:
  - sponsor:    Senior professionals at companies → ask for Soiree sponsorship
  - partner:    People at orgs → ask their org to become #SpeakingMyName partner
  - individual: Everyone → invite to attend the Soiree

Usage:
  python prepare_leads.py                           # push all campaigns to Google Sheets
  python prepare_leads.py --campaign sponsor         # sponsor only
  python prepare_leads.py --mode local               # write local .xlsx instead
  python prepare_leads.py --dry-run                  # preview without writing
"""

import argparse
import os
import sys

# Load .env from speakhire-outreach-simple or root
from dotenv import load_dotenv
for env_dir in ["speakhire-outreach-simple", "speakhire-outreach-shared", "."]:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), env_dir, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break
else:
    load_dotenv()  # fallback: try current directory
import pandas as pd
from typing import List, Dict, Optional

# Add campaign prompts to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from campaign_prompts import (
    CAMPAIGN_TYPES, get_sender, get_meta, CAMPAIGN_META,
)

# ============================================================================
# CONFIG
# ============================================================================
CHAMPIONS_CSV = "champions.csv"
INTERNS_CSV = "interns.csv"
OUTPUT_XLSX = "outreach_leads.xlsx"

# Keywords that indicate a senior/decision-maker role (likely to have budget authority)
# NOTE: Order matters for short acronyms — longer patterns checked first in match function
SENIOR_TITLE_KEYWORDS = [
    # C-suite / Executive (use word-boundary check for short acronyms)
    "Chief Executive Officer", "Chief Financial Officer", "Chief Operating Officer",
    "Chief Technology Officer", "Chief Information Officer", "Chief Marketing Officer",
    "Chief People Officer", "Chief Revenue Officer", "Chief Strategy Officer",
    "Chief", "President", "Vice President",
    # Full titles first (before short forms that could false-match)
    "Executive Director", "Managing Director", "General Manager",
    "Senior Director", "Senior Vice President", "Senior Manager",
    "Global Head", "Global Director",
    # Level indicators
    "Director", "Head of", "Partner", "Principal", "Owner", "Founder",
    "Global ", "Senior ", "Lead ",
    # Manager variants — keep "Manager" broad; it's better to over-classify than miss sponsors
    "Manager", "Supervisor",
    # Other senior indicators
    "Distinguished", "Fellow", "VP ", "SVP ", "EVP ", "AVP ",
    # C-level acronyms (last, to avoid false matches like "CTO" in "Director")
    " CEO", " CFO", " COO", " CTO", " CIO", " CMO", " CHRO", " CPO",
]

# Keywords that suggest an org-focused role (not purely individual)
ORG_ROLE_KEYWORDS = SENIOR_TITLE_KEYWORDS + [
    "Manager", "Associate", "Assistant", "Coordinator", "Specialist",
    "Consultant", "Analyst", "Representative", "Officer", "Administrator",
    "Professor", "Teacher", "Instructor", "Faculty", "Researcher",
    "Scientist", "Engineer", "Developer", "Designer", "Architect",
    "Attorney", "Lawyer", "Counsel", "Agent", "Broker", "Advisor",
    "Account", "Recruiter", "Strategist", "Planner", "Editor",
    "Producer", "Director", "Writer", "Photographer", "Artist",
    "Nurse", "Physician", "Doctor", "Pharmacist", "Therapist",
    "Fellow", "Intern", "Student", "Volunteer", "Staff",
]

COMPANY_BLACKLIST_SUBSTRINGS = [
    # These are checked as substrings (lowercase) — any match = not a real company
    "n/a", "freelanc", "self employed", "self-employed", "self employee",
    "unemployed", "retired", "own business", "my own", "independent",
    "not applicable", "job seeker", "autoemployed", "student",
    # "na" alone is too aggressive (matches "Nana Technologies", "Nassau", etc.)
    # Use it only as an exact match below
]

# Exact-match blacklist (lowercase)
COMPANY_BLACKLIST_EXACT = {
    "na", "none", "self", "n/a", "-", "---", "nil", "null",
}


def is_senior_title(title: str) -> bool:
    """Check if a job title suggests budget/decision-making authority."""
    t = str(title).strip()
    if not t or t.lower() in ("nan", "none", "null", "-", "n/a"):
        return False
    t_upper = t.upper()
    for kw in SENIOR_TITLE_KEYWORDS:
        if kw.upper() in t_upper:
            return True
    return False


def has_company(company: str) -> bool:
    """Check if company name is a real organisation (not self-employed/student)."""
    c = str(company).strip().lower()
    if not c:
        return False
    # Exact match blacklist
    if c in COMPANY_BLACKLIST_EXACT:
        return False
    # Substring blacklist
    for bad in COMPANY_BLACKLIST_SUBSTRINGS:
        if bad in c:
            return False
    return True


def is_potential_org(company: str, title: str) -> bool:
    """Check if person is at an org that could be approached for partnership."""
    return has_company(company) and bool(str(title).strip())


def classify_contact(row: Dict, source: str) -> List[str]:
    """
    Classify a contact into campaign types.
    Returns a list of campaign types this contact qualifies for.
    """
    campaigns = []

    # Everyone gets the individual campaign (Soiree invite)
    campaigns.append("individual")

    if source == "champions":
        company = str(row.get("company_name", "")).strip()
        title = str(row.get("job_title", "")).strip()

        if has_company(company):
            # Anyone at a real company → partner campaign (org-level)
            campaigns.append("partner")

            if is_senior_title(title):
                # Senior people → sponsor campaign
                campaigns.append("sponsor")

    return campaigns


def extract_first_last(name: str) -> tuple:
    """Split a full name into first and last name."""
    parts = str(name).strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _valid(val) -> str:
    """Return cleaned string, or empty if val is NaN/None/empty."""
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "null", "nat", "-", "--", "---", "false", "true", ""):
        return ""
    return s


def build_profile_notes(row: Dict, source: str) -> str:
    """Build a human-readable profile summary for the NOTES column."""
    parts = []

    if source == "champions":
        langs = _valid(row.get("languages", ""))
        career = _valid(row.get("main_career_field", ""))
        all_careers = _valid(row.get("career_fields", ""))
        title = _valid(row.get("job_title", ""))
        company = _valid(row.get("company_name", ""))
        interests = _valid(row.get("program_interests", ""))

        if langs:
            parts.append(f"Languages: {langs}")
        if career:
            parts.append(f"Career field: {career}")
        elif all_careers:
            parts.append(f"Career fields: {all_careers}")
        if title:
            parts.append(f"Title: {title}")
        if company:
            parts.append(f"Company: {company}")
        if interests:
            parts.append(f"Program interests: {interests}")
    else:
        langs = _valid(row.get("languages", ""))
        interests = _valid(row.get("career_interests", ""))
        future_job = _valid(row.get("ideal_future_job", ""))

        if langs:
            parts.append(f"Languages: {langs}")
        if interests:
            parts.append(f"Career interests: {interests}")
        if future_job:
            parts.append(f"Ideal future job: {future_job}")

    return " | ".join(parts) if parts else ""


def load_champions(path: str) -> List[Dict]:
    """Load and clean champions.csv."""
    df = pd.read_csv(path, encoding="utf-8")
    # Drop fully empty rows
    df = df.dropna(subset=["name"], how="all")
    df = df[df["name"].notna() & (df["name"].str.strip() != "")]
    return df.to_dict("records")


def load_interns(path: str) -> List[Dict]:
    """Load and clean interns.csv."""
    df = pd.read_csv(path, encoding="utf-8")
    df = df.dropna(subset=["name"], how="all")
    df = df[df["name"].notna() & (df["name"].str.strip() != "")]
    return df.to_dict("records")


def build_outreach_rows(
    champions: List[Dict],
    interns: List[Dict],
    selected_campaigns: List[str],
) -> List[Dict]:
    """
    Convert champion/intern records into outreach tracker rows.
    Each contact × each campaign they qualify for = one row.
    """
    rows = []
    seen = set()  # dedupe: (campaign, name, company)

    for source, contacts in [("champions", champions), ("interns", interns)]:
        for c in contacts:
            name = str(c.get("name", "")).strip()
            if not name:
                continue

            # Skip example/test rows
            name_lower = name.lower()
            if any(skip in name_lower for skip in ["sample", "test", "example", "champions example"]):
                continue

            first, last = extract_first_last(name)
            profile_notes = build_profile_notes(c, source)
            company = str(c.get("company_name", "")).strip() if source == "champions" else ""
            title = str(c.get("job_title", "")).strip() if source == "champions" else ""

            # Determine campaigns for this contact
            contact_campaigns = classify_contact(c, source)
            # Filter to selected campaigns
            contact_campaigns = [cm for cm in contact_campaigns if cm in selected_campaigns]

            for campaign in contact_campaigns:
                meta = get_meta(campaign)
                sender = get_sender(campaign)

                # Build unique key for deduplication
                key = (campaign, name, company)
                if key in seen:
                    continue
                seen.add(key)

                row = {
                    "ORG_NAME": company if meta["target_has_org"] else name,
                    "ORG_WEBSITE": "",  # User fills in or worker researches
                    "RECIPIENT": name,
                    "EMAIL": "",  # User fills in or worker finds
                    "CONTACT_FIRST_NAME": first,
                    "CONTACT_LAST_NAME": last,
                    "STATUS": "READY_FOR_RESEARCH",
                    "CAMPAIGN_TYPE": campaign,  # dropdown: sponsor / partner / individual
                    "NOTES": profile_notes,
                    "PERSONALISED_OPENER": "",
                    "EMAIL_SUBJECT": "",
                    "EMAIL_DRAFT": "",
                    "SENDER_NAME": sender["name"],
                    "SENDER_TITLE": sender["title"],
                    "SENDER_ORG": sender["org"],
                    "OPT_OUT": "FALSE",
                    "ERROR": "",
                    "CONTACT_PAGE_URL": "",
                    "ORG_TYPE": "company" if has_company(company) else "individual",
                    "SEGMENT": meta["segment"] if meta["target_has_org"] else f"Individual invite | {profile_notes}",
                    "PRIORITY": "HIGH" if campaign == "sponsor" else "MEDIUM",
                    "RESEARCH_QUERY": "",
                    "EVIDENCE_TITLE": "",
                    "EVIDENCE_SUMMARY": "",
                    "SOURCE_URL": "",
                    "SOURCE_DATE": "",
                    "RELEVANT_THEME": meta["description"],
                    "EVIDENCE_CONFIDENCE": "",
                    "CTA_TYPE": meta["cta_type"],
                    "CALL_DURATION": meta["call_duration"],
                    "LAST_UPDATED": "",
                }
                rows.append(row)

    return rows


# ============================================================================
# OUTPUT
# ============================================================================
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


def write_local(rows: List[Dict], path: str) -> None:
    """Write rows to a local .xlsx file."""
    data = [{c: r.get(c, "") for c in ROW_COLUMNS} for r in rows]
    df = pd.DataFrame(data, columns=ROW_COLUMNS)
    df.to_excel(path, index=False)
    print(f"Wrote {len(rows)} rows to {path}")


def write_google_sheets(rows: List[Dict], sheet_url: str, creds_path: str) -> None:
    """Write rows to Google Sheets."""
    import gspread
    import re

    m = re.search(r"/d/([a-zA-Z0-9\-_]+)", sheet_url)
    if not m:
        raise ValueError(f"Bad sheet URL: {sheet_url}")
    sheet_id = m.group(1)

    gc = gspread.service_account(filename=creds_path)
    sh = gc.open_by_key(sheet_id)

    # Get or create the Outreach Tracker worksheet
    try:
        ws = sh.worksheet("Outreach Tracker")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet("Outreach Tracker", rows=100, cols=len(ROW_COLUMNS))

    # Resize to fit all rows + header
    needed_rows = len(rows) + 2
    ws.resize(rows=needed_rows, cols=len(ROW_COLUMNS))

    # Write header
    ws.update(values=[ROW_COLUMNS], range_name="A1")

    # Write data in batches (with delay to avoid rate limits: 60 writes/min)
    import time
    batch_size = 50
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        data = [[r.get(c, "") for c in ROW_COLUMNS] for r in batch]
        start_row = i + 2  # row 1 = header
        ws.update(values=data, range_name=f"A{start_row}")
        print(f"  Uploaded rows {i + 1}-{min(i + batch_size, len(rows))}...")
        if i + batch_size < len(rows):
            time.sleep(1.2)  # stay under 60 writes/min

    print(f"Wrote {len(rows)} rows to Google Sheets: {sheet_url}")


# ============================================================================
# MAIN
# ============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Convert SpeakHire CSVs into outreach tracker format"
    )
    parser.add_argument(
        "--campaign", "-c",
        choices=CAMPAIGN_TYPES + ["all"],
        default="all",
        help="Which campaign type to generate leads for (default: all)",
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["local", "google"],
        default="google",
        help="Output mode: local .xlsx or Google Sheets (default: google)",
    )
    parser.add_argument(
        "--output", "-o",
        default=OUTPUT_XLSX,
        help=f"Output .xlsx path (default: {OUTPUT_XLSX})",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Preview only, don't write anything",
    )
    parser.add_argument(
        "--sheet-url",
        default=os.getenv("GOOGLE_SHEET_URL", ""),
        help="Google Sheet URL (for --mode google)",
    )
    parser.add_argument(
        "--creds",
        default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "autoemail-speakhire-fe7a5a191f0f.json"),
        help="Path to Google service account JSON",
    )
    args = parser.parse_args()

    # Determine campaigns to generate
    if args.campaign == "all":
        campaigns = list(CAMPAIGN_TYPES)
    else:
        campaigns = [args.campaign]

    print(f"Campaigns: {', '.join(campaigns)}")
    print(f"Mode: {args.mode}")
    print()

    # Load CSVs
    print("Loading champions.csv...")
    champions = load_champions(CHAMPIONS_CSV)
    print(f"  {len(champions)} contacts loaded")

    print("Loading interns.csv...")
    interns = load_interns(INTERNS_CSV)
    print(f"  {len(interns)} contacts loaded")

    # Classify and build rows
    print("\nClassifying contacts...")
    rows = build_outreach_rows(champions, interns, campaigns)

    # Stats
    counts = {}
    for r in rows:
        tag = r["CAMPAIGN_TYPE"]
        counts[tag] = counts.get(tag, 0) + 1
    print(f"Generated {len(rows)} total outreach rows:")
    for cm in CAMPAIGN_TYPES:
        print(f"  {CAMPAIGN_META[cm]['label']}: {counts.get(cm, 0)} rows")

    if not rows:
        print("\nNo leads generated. Check your CSV data and campaign selection.")
        return

    if args.dry_run:
        print("\n--dry-run: Preview of first 3 rows:")
        for i, r in enumerate(rows[:3]):
            print(f"\n  Row {i + 1}:")
            print(f"    Campaign:   {r['CAMPAIGN_TYPE']}")
            print(f"    Recipient:  {r['RECIPIENT']}")
            print(f"    Org:        {r['ORG_NAME']}")
            print(f"    Notes:      {r['NOTES'][:120]}...")
            print(f"    Priority:   {r['PRIORITY']}")
        return

    # Write output
    if args.mode == "local":
        write_local(rows, args.output)
        print(f"\nNext step: run the outreach worker on this file:")
        print(f"  python outreach_worker.py")
    else:
        if not args.sheet_url:
            print("ERROR: --sheet-url required for Google Sheets mode")
            sys.exit(1)
        write_google_sheets(rows, args.sheet_url, args.creds)
        print(f"\nNext step: review leads in Google Sheets, then run:")
        print(f"  python outreach_worker.py --mode google")


if __name__ == "__main__":
    main()
