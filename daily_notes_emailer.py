#!/usr/bin/env python3
"""
Daily Notes Emailer
Picks a random date-keyed entry from Apple Notes "Daily Archive" and sends it as HTML email.
"""
import html
import json
import logging
import os
import random
import re
import smtplib
import subprocess
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.json')
LOG_PATH = os.path.join(SCRIPT_DIR, 'emailer.log')
MAX_PLAIN_CHARS = 3000

logging.basicConfig(
    filename=LOG_PATH, level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
log = logging.getLogger(__name__)

# Matches dates like: 09/01/2026 | Sep 1, 2026 | September 1, 2026 | 2026-09-01
# with optional day-of-week prefix and optional trailing colon/space
DATE_TEXT_RE = re.compile(
    r'^(?:(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|'
    r'Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)[,\s]+)?'
    r'(?:\d{1,2}/\d{1,2}/\d{2,4}'
    r'|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
    r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|'
    r'Dec(?:ember)?)\.?\s+\d{1,2}(?:st|nd|rd|th)?[,\s]+\d{4}'
    r'|\d{4}-\d{2}-\d{2})',
    re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Apple Notes access
# ---------------------------------------------------------------------------

GET_NOTE_LIST_SCRIPT = '''\
tell application "Notes"
    set out to ""
    set arch to missing value
    repeat with acct in accounts
        try
            set arch to folder "Daily Archive" of acct
            exit repeat
        end try
    end repeat
    if arch is missing value then return "ERR:no folder"
    -- notes directly in Daily Archive (no sub-folders in this setup)
    repeat with n in (notes of arch)
        set out to out & (id of n) & "|" & (name of n) & linefeed
    end repeat
    -- also check sub-folders in case they exist
    repeat with mf in (folders of arch)
        repeat with n in (notes of mf)
            set out to out & (id of n) & "|" & (name of n) & linefeed
        end repeat
    end repeat
    return out
end tell'''


def get_note_list():
    r = subprocess.run(['osascript', '-e', GET_NOTE_LIST_SCRIPT],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or 'osascript error listing notes')
    out = r.stdout.strip()
    if out.startswith('ERR:'):
        raise RuntimeError(out[4:])
    notes = []
    for line in out.splitlines():
        if '|' in line:
            nid, _, title = line.partition('|')
            notes.append((nid.strip(), title.strip()))
    return notes


def get_note_body(note_id):
    safe = note_id.replace('\\', '\\\\').replace('"', '\\"')
    script = f'tell application "Notes"\nreturn body of note id "{safe}"\nend tell'
    r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or 'osascript error fetching note body')
    return r.stdout


# ---------------------------------------------------------------------------
# HTML parsing: split note body into date-keyed sections
# ---------------------------------------------------------------------------

def _div_text(div_html):
    """Extract plain text from a single div's HTML."""
    inner = re.sub(r'^<div[^>]*>', '', div_html, count=1, flags=re.IGNORECASE)
    inner = re.sub(r'</div>\s*$', '', inner, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', inner)
    return html.unescape(text).strip()


def _split_top_level_elements(body):
    """
    Yield (raw_html_block) for each top-level element in the note body.
    Handles <div> (possibly with inline tags inside) and <ul>/<ol> blocks.
    Apple Notes HTML is a flat sequence of <div> and <ul>/<ol> blocks.
    """
    # Extract content between <body> and </body>
    m = re.search(r'<body[^>]*>(.*?)</body>', body, re.DOTALL | re.IGNORECASE)
    content = m.group(1) if m else body

    pos = 0
    block_re = re.compile(r'<(div|ul|ol)(?:\s[^>]*)?>', re.IGNORECASE)

    while pos < len(content):
        m = block_re.search(content, pos)
        if not m:
            break
        tag = m.group(1).lower()
        close_tag = f'</{tag}>'

        # For divs: find first </div> after open (they don't nest in Notes)
        # For ul/ol: find first </ul> or </ol>
        end = content.find(close_tag, m.end())
        if end == -1:
            pos = m.end()
            continue

        block = content[m.start(): end + len(close_tag)]
        yield block
        pos = end + len(close_tag)


def find_date_sections(note_body):
    """
    Return a list of dicts: {'date': str, 'blocks': [raw_html, ...]}
    Each section starts with the date-header div and includes all subsequent
    blocks up to (but not including) the next date-header.
    """
    sections = []
    current = None

    for block in _split_top_level_elements(note_body):
        # Only check div blocks for date headers
        if block.lstrip().lower().startswith('<div'):
            text = _div_text(block)
            if DATE_TEXT_RE.match(text):
                if current is not None:
                    sections.append(current)
                # Clean up the date text: remove trailing colon
                date_label = text.rstrip(':').strip()
                current = {'date': date_label, 'blocks': [block]}
                continue

        if current is not None:
            current['blocks'].append(block)

    if current is not None:
        sections.append(current)

    return sections


# ---------------------------------------------------------------------------
# Trim section to MAX_PLAIN_CHARS
# ---------------------------------------------------------------------------

def _plain_len(raw_html):
    return len(html.unescape(re.sub(r'<[^>]+>', '', raw_html)))


def trim_section(section, max_chars=MAX_PLAIN_CHARS):
    """Return section blocks trimmed so total plain-text chars <= max_chars."""
    total = 0
    kept = []
    for block in section['blocks']:
        plen = _plain_len(block)
        if total + plen > max_chars:
            if not kept:
                kept.append(block)  # always keep at least the date header
            break
        kept.append(block)
        total += plen
    return kept


# ---------------------------------------------------------------------------
# Render HTML email
# ---------------------------------------------------------------------------

def render_email_html(section, note_title, blocks):
    date_str = html.escape(section['date'])
    source = html.escape(note_title)
    now = datetime.now()
    today = f"{now.strftime('%B')} {now.day}, {now.year}"

    # Build the inner HTML from the blocks, skipping the date header div itself
    inner = '\n'.join(blocks[1:])  # first block is the date header

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  body {{
    font-family: Georgia, serif;
    max-width: 660px;
    margin: 36px auto;
    color: #222;
    line-height: 1.75;
    padding: 0 24px;
  }}
  h2 {{
    font-size: 1.25em;
    color: #444;
    font-weight: normal;
    border-bottom: 1px solid #ddd;
    padding-bottom: .4em;
    margin-bottom: .3em;
  }}
  .source {{
    font-size: .8em;
    color: #aaa;
    margin-bottom: 1.6em;
  }}
  div {{ margin: 0; padding: 0; }}
  ul, ol {{ margin: .5em 0 1em 1.5em; padding: 0; }}
  li {{ margin-bottom: .3em; }}
  footer {{
    margin-top: 2em;
    font-size: .75em;
    color: #bbb;
    border-top: 1px solid #eee;
    padding-top: 1em;
  }}
</style>
</head>
<body>
  <h2>{date_str}</h2>
  <div class="source">From: {source}</div>
  {inner}
  <footer>Daily Archive &middot; {today}</footer>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_email(subject, html_body, cfg):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = cfg['sender_email']
    msg['To'] = cfg['recipient_email']
    msg.attach(MIMEText(html_body, 'html'))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as srv:
        srv.login(cfg['sender_email'], cfg['app_password'])
        srv.send_message(msg)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(dry_run=False):
    log.info('run start')

    if not os.path.exists(CONFIG_PATH):
        sys.exit(f'No config at {CONFIG_PATH} — run install.sh first')
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

    notes = get_note_list()
    if not notes:
        sys.exit('No notes found in Daily Archive folder')
    log.info('%d notes available', len(notes))

    # Exclude the non-monthly "Reading Notes" etc. by trying all notes shuffled
    random.shuffle(notes)
    for note_id, note_title in notes:
        body = get_note_body(note_id)
        sections = find_date_sections(body)
        if not sections:
            log.info('no date sections in "%s", skipping', note_title)
            continue

        section = random.choice(sections)
        blocks = trim_section(section)
        email_html = render_email_html(section, note_title, blocks)
        subject = f"Daily Note · {section['date']}"

        if dry_run:
            print(f"Subject: {subject}")
            print(f"Note: {note_title}  ({len(sections)} sections)")
            print(f"Section date: {section['date']}")
            print(f"Blocks kept: {len(blocks)}/{len(section['blocks'])}")
            print("--- HTML ---")
            print(email_html[:800])
            return

        send_email(subject, email_html, cfg)
        log.info('sent: %s', subject)
        print(f"Sent: {subject}")
        return

    sys.exit('No date sections found in any note in Daily Archive')


if __name__ == '__main__':
    dry_run = '--dry-run' in sys.argv
    try:
        main(dry_run=dry_run)
    except Exception as e:
        log.exception('fatal error: %s', e)
        raise
