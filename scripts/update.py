#!/usr/bin/env python3
"""
One-command update script.

Usage:
    python scripts/update.py --chat ~/Downloads/_chat.txt [--chat file2.txt] [--push] [--url https://...]

What it does:
    1. Runs parse_chat.py against your chat file(s)
    2. Writes docs/data/stats.json
    3. Prints a ready-to-paste WhatsApp weekly summary
    4. With --push: commits stats.json and pushes to GitHub (triggers Vercel deploy)

The raw _chat.txt files are NEVER committed — only the computed stats JSON.
"""

import argparse
import glob
import os
import subprocess
import sys
from pathlib import Path

# Repo root = parent of this script's directory
REPO_ROOT = Path(__file__).parent.parent
PARSER = REPO_ROOT / 'scripts' / 'parse_chat.py'
OUTPUT = REPO_ROOT / 'docs' / 'data' / 'stats.json'

DEFAULT_URL = 'https://accountability-partners.vercel.app'


def find_all_chat_exports():
    """Auto-detect ALL WhatsApp Accountability Partners exports in ~/Downloads.

    We always want to parse every export together so the older file's #1 posts
    (Jan 1) are included — the parser deduplicates by (timestamp, author).
    Returns list sorted oldest-first (by mtime).
    """
    pattern = os.path.expanduser(
        '~/Downloads/WhatsApp Chat - Accountability Partners*/_chat.txt'
    )
    found = glob.glob(pattern)
    if not found:
        # Fallback: looser match
        for pat in [
            os.path.expanduser('~/Downloads/*accountability*/_chat.txt'),
            os.path.expanduser('~/Downloads/*Accountability*/_chat.txt'),
        ]:
            found.extend(glob.glob(pat))
    # Sort oldest first so deduplication keeps the original timestamps
    return sorted(set(found), key=os.path.getmtime)


def run(cmd, check=True):
    print(f'  $ {" ".join(str(c) for c in cmd)}')
    result = subprocess.run(cmd, capture_output=False, text=True)
    if check and result.returncode != 0:
        print(f'  ✗ Command failed (exit {result.returncode})', file=sys.stderr)
        sys.exit(result.returncode)
    return result


def main():
    parser = argparse.ArgumentParser(description='Update Accountability Partners stats site')
    parser.add_argument('--chat', dest='chats', action='append', metavar='FILE',
                        help='Path to WhatsApp _chat.txt export (can repeat for multiple files)')
    parser.add_argument('--push', action='store_true',
                        help='Commit stats.json and push to trigger Vercel deploy')
    parser.add_argument('--url', default=DEFAULT_URL, help='Site URL for weekly summary')
    args = parser.parse_args()

    chats = args.chats or []

    # Auto-detect if no files given — pick up ALL exports so older #1 posts
    # (Jan 1 data) are always included alongside the latest export.
    if not chats:
        chats = find_all_chat_exports()
        if chats:
            for c in chats:
                print(f'  Auto-detected: {c}')
        else:
            print('✗ No chat files found. Pass --chat /path/to/_chat.txt', file=sys.stderr)
            sys.exit(1)

    # Verify files exist
    for c in chats:
        if not Path(c).exists():
            print(f'✗ File not found: {c}', file=sys.stderr)
            sys.exit(1)

    print('\n── Step 1: Parse chat ──────────────────────────')
    cmd = [sys.executable, str(PARSER)] + chats + [
        '--output', str(OUTPUT),
        '--url', args.url,
    ]
    run(cmd)

    if args.push:
        print('\n── Step 2: Commit & push ───────────────────────')
        os.chdir(REPO_ROOT)
        from datetime import date
        today = date.today().isoformat()

        run(['git', 'add', 'docs/data/stats.json'])
        run(['git', 'commit', '-m', f'Stats update {today}'])
        run(['git', 'push'])
        print(f'\n✓ Deployed! Check {args.url} in ~30 seconds.')
    else:
        print(f'\n✓ Done. Open docs/index.html via HTTP to preview.')
        print(f'  To deploy: python scripts/update.py --chat <file> --push')


if __name__ == '__main__':
    main()
