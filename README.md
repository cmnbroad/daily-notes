# Daily Notes Emailer

A small macOS script that picks a random dated entry from your Apple Notes **Daily Archive** folder and emails it to you as a formatted HTML message. A LaunchAgent runs it every morning, so an old journal entry shows up in your inbox each day.

## How it works

1. Uses AppleScript (`osascript`) to find a folder named `Daily Archive` in any Notes account and list every note in it, including notes in its subfolders.
2. Shuffles the notes and reads the HTML body of each one in turn until it finds one with dated sections.
3. Splits the note into sections. A section starts at a line containing only a date and runs until the next date line. These date formats are recognized:
   - `09/01/2026`
   - `Sep 1, 2026` / `September 1st, 2026`
   - `2026-09-01`
   - Any of the above with a weekday in front (`Monday, Sep 1, 2026`) and/or a colon after it
4. Picks one section at random and trims it to about 3,000 characters of plain text, cutting at block boundaries.
5. Puts the section into a simple serif HTML email and sends it through Gmail SMTP (SSL, port 465). The subject is `Daily Note · <date>`.

## Requirements

- macOS with the Notes app and a folder named `Daily Archive`
- Python 3 (standard library only, nothing to install)
- A Gmail account with 2-Step Verification turned on and an [App Password](https://myaccount.google.com/apppasswords)

## Installation

```bash
./install.sh
```

The installer:

1. If `config.json` doesn't exist, asks for your Gmail address and App Password and writes them to it.
2. Writes a LaunchAgent to `~/Library/LaunchAgents/com.cmn.dailynotes.plist` that runs the script every day at **8:00 AM**.
3. Loads the agent with `launchctl` (if an older version was loaded, it is unloaded first).

The first time it runs, macOS will ask for permission to let Python/Terminal control Notes. Allow it, or the script won't be able to read your notes.

## Configuration

`config.json` (ignored by git; see `config.example.json`):

```json
{
  "sender_email": "your_gmail_address@gmail.com",
  "app_password": "xxxx xxxx xxxx xxxx"
}
```

Settings that are constants in `daily_notes_emailer.py`:

| Constant          | Purpose                                       |
|-------------------|-----------------------------------------------|
| `RECIPIENT`       | Address the email is sent to                  |
| `MAX_PLAIN_CHARS` | Rough character limit for each section (3000) |

To change the run time, edit `Hour` and `Minute` in `install.sh` and run it again.

## Usage

Preview without sending (prints the subject, the source note, and the first part of the HTML):

```bash
python3 daily_notes_emailer.py --dry-run
```

Send one now:

```bash
python3 daily_notes_emailer.py
```

## Logs

| File           | Contents                                   |
|----------------|--------------------------------------------|
| `emailer.log`  | Run history, skipped notes, and errors     |
| `launchd.out`  | stdout from scheduled runs                 |
| `launchd.err`  | stderr from scheduled runs                 |

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.cmn.dailynotes.plist
rm ~/Library/LaunchAgents/com.cmn.dailynotes.plist
```
