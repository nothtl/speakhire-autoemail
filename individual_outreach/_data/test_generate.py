"""Generate 3 test summit emails."""
import sys, os, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '../speakhire-outreach/speakhire-outreach-simple')

from dotenv import load_dotenv
load_dotenv(r'C:\Users\Tingli\Documents\GitHub\speakhire\autoemail\speakhire-outreach\speakhire-outreach-simple\.env')

import requests
from summit_prompt import SUMMIT_SYSTEM_PROMPT, SUMMIT_CONTEXT

API_KEY = os.getenv('DEEPSEEK_API_KEY')
BASE_URL = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')

test_contacts = [
    {
        'name': 'Ephraim',
        'career_interests': 'stem; arts; bus; estate',
        'ideal_job': '',
        'language': 'Yoruba',
        'age_hint': 'workforce seeker (20-26) — looking for internships and full-time roles',
    },
    {
        'name': 'Ismatu',
        'career_interests': 'stem; health',
        'ideal_job': '',
        'language': 'French',
        'age_hint': 'pathway builder (12-19) — exploring career options and building confidence',
    },
    {
        'name': 'Jaime',
        'career_interests': 'arts; ent',
        'ideal_job': '',
        'language': 'Spanish',
        'age_hint': 'workforce seeker (20-26) — looking for creative industry connections',
    },
]

for contact in test_contacts:
    user_prompt = f"""
Write a personalized Summit invitation email for:

Name: {contact['name']}
Languages spoken: {contact['language']}
Career interests: {contact['career_interests']}
Ideal future job: {contact['ideal_job'] or 'not specified'}
Age group: {contact['age_hint']}

{SUMMIT_CONTEXT}

Remember: be specific about THEIR interests. If they like STEM, talk about tech pathways. If they speak a language other than English, mention multilingual demand. Reference the panelist whose career matches their interests. Keep it under 130 words.
"""

    resp = requests.post(
        f'{BASE_URL}/chat/completions',
        headers={'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json'},
        json={
            'model': 'deepseek-chat',
            'messages': [
                {'role': 'system', 'content': SUMMIT_SYSTEM_PROMPT},
                {'role': 'user', 'content': user_prompt},
            ],
            'temperature': 0.7,
        },
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()['choices'][0]['message']['content'].strip()
    # Parse JSON from response
    if content.startswith('```'):
        content = content[content.find('\n'):].strip()
    if content.endswith('```'):
        content = content[:-3].strip()
    s, e = content.find('{'), content.rfind('}')
    if s != -1 and e != -1:
        content = content[s:e+1]
    result = json.loads(content)
    # Strip em dashes from all fields
    for key in result:
        if isinstance(result[key], str):
            result[key] = result[key].replace('—', '-').replace('–', '-')

    print(f"=== {contact['name']} ({contact['language']}) | interests: {contact['career_interests']} ===")
    print(f"Subject: {result.get('email_subject','')}")
    print()
    print(result.get('email_body',''))
    print()
    print('---')
    print()
