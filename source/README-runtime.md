# Slow Computer Rescue

A simple Linux utility for people who are frustrated because their computer feels slow.

The app explains the problem in plain language:

> Your computer might be slow because your storage is full. Want me to clean things up to help speed your computer up?

## What v1 does

- Shows a **Speed Check** with storage usage and RAM usage.
- Warns when storage is over 80% full.
- Warns when RAM usage is high.
- Scans selected cleanup areas before deleting anything.
- Shows cleanup category totals.
- Shows a visible file list for old Downloads before deletion.
- Cleans only after the user confirms.
- Shows before/after disk space when cleanup finishes.

## Cleanup options

Default v1 options:

- Empty Trash
- Clear browser cache
- Clear system temp files
- Clean Downloads older than 7 days

## Safety notes

- The app previews what it found before cleaning.
- Trash items are permanently removed when you choose Empty Trash.
- Downloads older than 7 days are listed in the preview before deletion.
- System temp cleanup is limited to user-owned temporary files older than 24 hours.
- The app does not use sudo and does not try to clean protected system folders.

## How to run

From this folder:

```bash
./Launch_Slow_Computer_Rescue
```

Then in the app:

1. Keep the cleanup checkboxes you want selected.
2. Click **Check & Clean My Computer**.
3. Review the cleanup preview.
4. Click **Clean Selected Items** when the main button changes.
5. Confirm cleanup.

Or run directly:

```bash
python3 slow_computer_rescue.py
```

## Self-test

Run a non-destructive integrity check:

```bash
./Launch_Slow_Computer_Rescue --self-test
```

Expected output in `/tmp/slow_computer_rescue_launcher.log`:

```text
SELF-TEST PASS
```

## Product direction

This is positioned as a simple speed-up helper, not a complicated power-user cleaner.

Customer-facing promise should be careful:

> Helps identify storage and memory pressure that can make a Linux computer feel slow, then offers simple cleanup options.

Avoid promising guaranteed speed improvement on every computer.
