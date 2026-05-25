#!/usr/bin/env python3
"""
Parse WhatsApp Accountability Partners chat → docs/data/stats.json

Usage:
    python scripts/parse_chat.py file1.txt [file2.txt ...] [--output docs/data/stats.json]

Multiple files are merged and deduplicated by (timestamp, author) before parsing.
The _chat.txt file is never committed — only stats.json is.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

NAME_MAP = {
    'Sagar Daliya': 'Sagar',
    'Sahil Gupta': 'Sahil',
    'Rashmi': 'Rashmi',
    'Beba\U0001F42F': 'Riya',   # Beba🐯 = Riya (different from Smriti)
    'Beba🐯': 'Riya',
    'Rheakasset': 'Riya',       # alternative username
    'Smriti': 'Smriti',
    'Harshvardhan Agarwal': 'Harsh',
    'Sharad Ranghar': 'Sharad',
    'Sharangdhar': 'Sharad',
    'Arjit Khandelwal': 'Arjit',
    'Nikhar Maheshwari': 'Nikhar',
    'Ekansh Jain': 'Ekansh',
    'Ekansh': 'Ekansh',
    'Jindal': 'Jindal',
}

PERSON_CONFIG = {
    'Arjit':  {'emoji': '💪', 'color': '#FF6B6B'},
    'Sagar':  {'emoji': '🏏', 'color': '#6C63FF'},
    'Rashmi': {'emoji': '🏃', 'color': '#43E97B'},
    'Sharad': {'emoji': '⚡', 'color': '#FFB347'},
    'Sahil':  {'emoji': '🏋️', 'color': '#FF6584'},
    'Harsh':  {'emoji': '🏊', 'color': '#4FC3F7'},
    'Nikhar': {'emoji': '🎯', 'color': '#A8E063'},
    'Riya':   {'emoji': '🐯', 'color': '#FA709A', 'backfill': True},
    'Smriti': {'emoji': '💃', 'color': '#C084FC'},
    'Ekansh': {'emoji': '🧘', 'color': '#B8B8FF'},
    'Jindal': {'emoji': '🦁', 'color': '#FFA07A'},
}

# Shown only in leaderboard, not individual stat cards
CARDS_EXCLUDE = {'Smriti', 'Ekansh', 'Jindal'}

# ── Activity Categorisation ───────────────────────────────────────────────────
# Keywords are matched case-insensitively against the full workout message text.
# First category that matches wins; longest / most specific keywords are listed
# first within each group so they take priority over short generic ones.

ACTIVITY_CATEGORIES = {
    'Cardio': [
        'treadmill', 'running', 'jogging', 'jog', 'hiit', 'dance cardio',
        'dance fitness', 'dance workout', 'dance', 'swimming', 'swim',
        'hiking', 'hike', 'cycling', 'cycle', 'skipping', 'jump rope',
        'stair', 'run', 'cardio', 'k steps', 'steps', 'walk', 'k+',
    ],
    'Strength': [
        'pull day', 'push day', 'leg day', 'upper body', 'lower body',
        'deadlift', 'bench press', 'squat', 'weights', 'lifting', 'lift',
        'shoulder', 'chest', 'pull', 'push', 'arms', 'back', 'legs',
        'strength', 'gym',
    ],
    'Flexibility': [
        'physiotherapy', 'physio', 'pilates', 'stretching', 'stretch',
        'mobility', 'flexibility', 'yoga',
    ],
    'Sports': [
        'table tennis', 'badminton', 'cricket', 'football', 'basketball',
        'squash', 'tennis', 'sport', 'match',
    ],
}


def categorize_activity(text: str) -> str:
    """Return the best-matching category for a workout message, or 'General'."""
    t = text.lower()
    for cat, keywords in ACTIVITY_CATEGORIES.items():
        for kw in keywords:
            if kw in t:
                return cat
    return 'General'

# ── Parsing ───────────────────────────────────────────────────────────────────

MSG_RE = re.compile(
    r'[‎]?\[(\d{2}/\d{2}/\d{2}),\s+(\d+:\d{2}:\d{2}\s+[AP]M)\]\s+([^:]+):\s+(.*)',
)
HASH_RE = re.compile(r'#(\d+)([^\n#₹]*)', re.IGNORECASE)


def parse_messages(filepaths):
    """Parse one or more chat files, deduplicate by (ts_key, author), sort by time."""
    seen = set()
    messages = []

    for fp in filepaths:
        path = Path(fp)
        if not path.exists():
            print(f'  Warning: {fp} not found, skipping', file=sys.stderr)
            continue

        with open(path, encoding='utf-8') as f:
            lines = f.readlines()

        current = None
        for line in lines:
            line = line.rstrip('\n')
            m = MSG_RE.match(line)
            if m:
                if current:
                    key = (current['ts_key'], current['author'])
                    if key not in seen:
                        seen.add(key)
                        messages.append(current)
                date_str, time_str, author, text = m.groups()
                try:
                    dt = datetime.strptime(f'{date_str} {time_str}', '%d/%m/%y %I:%M:%S %p')
                except ValueError:
                    current = None
                    continue
                current = {
                    'dt': dt,
                    'ts_key': f'{date_str}_{time_str}',
                    'author': author.strip(),
                    'text': text.strip(),
                }
            elif current:
                current['text'] += '\n' + line

        if current:
            key = (current['ts_key'], current['author'])
            if key not in seen:
                seen.add(key)
                messages.append(current)

    messages.sort(key=lambda m: m['dt'])
    return messages


def extract_workout_entries(messages):
    """Return {person: [(workout_date, workout_num, category), ...]} for 2026 onwards.

    Any author not in NAME_MAP who posts a #N tag is auto-included under their
    raw WhatsApp name (first word, title-cased) and a warning is printed so the
    NAME_MAP can be updated for future runs.
    """
    entries = defaultdict(list)
    unknown_warned = set()

    for msg in messages:
        if msg['dt'].year < 2026:
            continue

        raw_author = msg['author']
        author = NAME_MAP.get(raw_author)

        # Auto-discover: include unknown authors who post #N tags
        if not author:
            text_probe = msg['text']
            if HASH_RE.search(text_probe):
                if raw_author not in unknown_warned:
                    # Derive a short display name (first word, title-cased)
                    display = raw_author.split()[0].title()
                    print(f'  ⚠ Unknown author posting #N: {repr(raw_author)} → using "{display}"',
                          file=sys.stderr)
                    print(f'    Add to NAME_MAP: {repr(raw_author)!r}: {repr(display)!r}',
                          file=sys.stderr)
                    unknown_warned.add(raw_author)
                    # Register dynamically so we capture their data
                    NAME_MAP[raw_author] = raw_author.split()[0].title()
                author = NAME_MAP.get(raw_author)
            if not author:
                continue

        text = msg['text']
        msg_date = msg['dt'].date()

        for m in HASH_RE.finditer(text):
            num_str = m.group(1)
            context = m.group(2).strip().lower()

            # Skip ₹ immediately before #
            pos = m.start()
            if pos > 0 and text[pos - 1] == '₹':
                continue

            # Skip Sahil's (or anyone's) diet counter
            if context.startswith('diet') or ' diet' in context[:14]:
                continue

            num = int(num_str)

            # Infer actual workout date from context
            workout_date = msg_date
            if 'day before yesterday' in context or 'dby' in context:
                workout_date = msg_date - timedelta(days=2)
            elif 'yday' in context or 'yesterday' in context:
                workout_date = msg_date - timedelta(days=1)
            # Day-name hints (e.g. "Tuesday", "Saturday")
            # Not worth the complexity — skip, use message date

            # Build combined text for activity categorisation.
            # group(2) only captures same-line text after #N (stops at \n).
            # text[m.end():] catches descriptions on the next line, which is
            # how Arjit (and others) post:  "#123\nPull day" or "#121\n10k steps"
            before_text = text[:m.start()].strip()
            after_text  = (m.group(2) + ' ' + text[m.end():]).strip()
            full_activity = f'{before_text} {after_text}'.strip()
            category = categorize_activity(full_activity)

            entries[author].append((workout_date, num, category))

    return entries


# ── Stats ─────────────────────────────────────────────────────────────────────

def calc_streaks(dates_set):
    if not dates_set:
        return 0, 0
    sorted_dates = sorted(dates_set)
    today = date.today()

    # Best streak
    best = cur = 1
    for i in range(1, len(sorted_dates)):
        delta = (sorted_dates[i] - sorted_dates[i - 1]).days
        if delta == 1:
            cur += 1
            best = max(best, cur)
        elif delta > 1:
            cur = 1
    best = max(best, cur)

    # Current streak (only if last workout ≤ 1 day ago)
    last = sorted_dates[-1]
    if (today - last).days > 1:
        return best, 0

    current = 1
    for i in range(len(sorted_dates) - 1, 0, -1):
        if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
            current += 1
        else:
            break
    return best, current


def compute_timeseries(entries, backfill=False):
    """Monotonically-increasing {date, count} list for the race chart.

    Only emits a point when the count increases, so Chart.js spanGaps fills
    the flat parts.

    backfill=True  → baseline is Jan 1 (for members who were doing workouts
                     before they started posting — the chart shows estimated
                     steady activity from the start of the year).
    backfill=False → baseline is 1 day before first post, so late starters
                     don't get a fake straight line projected back to January.
    """
    if not entries:
        return []

    # Max workout number per day (entries may be 2- or 3-tuples)
    by_date = {}
    for item in entries:
        d, n = item[0], item[1]
        by_date[d] = max(by_date.get(d, 0), n)

    first_date = min(by_date.keys())
    if backfill:
        baseline = date(2026, 1, 1)
    else:
        # 1 day before first post, clamped to Jan 1
        baseline = max(date(2026, 1, 1), first_date - timedelta(days=1))
    result = [{'date': baseline.isoformat(), 'count': 0}]

    running_max = 0
    for d in sorted(by_date.keys()):
        n = by_date[d]
        if n > running_max:
            running_max = n
            result.append({'date': d.isoformat(), 'count': running_max})

    # Add today's endpoint so all lines reach the same x
    today_iso = date.today().isoformat()
    if result[-1]['date'] != today_iso:
        result.append({'date': today_iso, 'count': running_max})

    return result


def compute_achievements(total, best_streak, monthly_counts):
    badges = []
    for t in [25, 50, 75, 100, 125]:
        if total >= t:
            badges.append(f'w{t}')
    for t in [7, 14, 30]:
        if best_streak >= t:
            badges.append(f's{t}')
    if any(v >= 20 for v in monthly_counts.values()):
        badges.append('consistent_month')
    return badges


def build_stats(entries):
    today = date.today()
    all_months = set()
    person_stats = {}

    for person, workout_list in entries.items():
        if not workout_list:
            continue

        dates_set = set(item[0] for item in workout_list)
        total = max(item[1] for item in workout_list)

        # Infer actual first workout date from the workout counter.
        # If someone's first post was "#23" on Jan 23, they'd done 22 workouts
        # before joining the chat → inferred start = Jan 23 − 22 days = Jan 1.
        # Clamped to Jan 1 so we never go before the year.
        wlist_sorted_by_date = sorted(workout_list, key=lambda x: (x[0], x[1]))
        first_post_date = wlist_sorted_by_date[0][0]
        first_post_num  = wlist_sorted_by_date[0][1]
        first_workout_date = max(date(2026, 1, 1),
                                 first_post_date - timedelta(days=(first_post_num - 1)))

        # Activity category counts (only one entry per date to avoid over-counting)
        category_counts: dict[str, int] = defaultdict(int)
        seen_cat_dates: set = set()
        for item in workout_list:
            d, n, cat = item[0], item[1], item[2] if len(item) > 2 else 'General'
            # Count each workout date once per category bucket
            key = (d, cat)
            if key not in seen_cat_dates:
                seen_cat_dates.add(key)
                category_counts[cat] += 1

        # Monthly unique workout days
        monthly = defaultdict(int)
        seen_by_month = defaultdict(set)
        for item in workout_list:
            d = item[0]
            mk = d.strftime('%Y-%m')  # type: ignore[attr-defined]
            all_months.add(mk)
            if d not in seen_by_month[mk]:
                seen_by_month[mk].add(d)
                monthly[mk] += 1

        # Time windows
        cutoff30 = today - timedelta(days=30)
        cutoff7 = today - timedelta(days=7)
        month_start = today.replace(day=1)

        last30 = sum(1 for d in dates_set if d >= cutoff30)
        last7 = sum(1 for d in dates_set if d >= cutoff7)
        this_month = sum(1 for d in dates_set if d >= month_start)

        best_streak, current_streak = calc_streaks(dates_set)

        last_date = max(dates_set)
        days_since = (today - last_date).days

        person_stats[person] = {
            'total': total,
            'monthly': dict(monthly),
            'last30': last30,
            'last7': last7,
            'weekly': last7,
            'this_month': this_month,
            'best_streak': best_streak,
            'current_streak': current_streak,
            'last_date': last_date.isoformat(),
            'days_since': days_since,
            'timeseries': compute_timeseries(
                workout_list,
                backfill=PERSON_CONFIG.get(person, {}).get('backfill', False),
            ),
            'first_workout_date': first_workout_date.isoformat(),
            'workout_dates': sorted(d.isoformat() for d in dates_set),
            'achievements': compute_achievements(total, best_streak, dict(monthly)),
            'activity_categories': dict(category_counts),
            'emoji': PERSON_CONFIG.get(person, {}).get('emoji', ''),
            'color': PERSON_CONFIG.get(person, {}).get('color', '#888'),
            'in_cards': person not in CARDS_EXCLUDE,
        }

    return person_stats, sorted(all_months)


def compute_spotlight(person_stats):
    today = date.today()
    week_start = today - timedelta(days=6)

    # Most active this week
    active = [(p, s['weekly']) for p, s in person_stats.items() if s['weekly'] > 0]
    most_active = max(active, key=lambda x: x[1]) if active else None

    # Milestones crossed this week
    milestones = []
    for person, stats in person_stats.items():
        ts = stats['timeseries']
        if not ts:
            continue
        # Find count 7 days ago
        count_before = 0
        for pt in ts:
            if pt['date'] < week_start.isoformat():
                count_before = pt['count']
        for m in [25, 50, 75, 100, 125]:
            if stats['total'] >= m and count_before < m:
                milestones.append({'name': person, 'number': m})

    # Comebacks: worked out this week after >14 day gap
    comebacks = []
    for person, stats in person_stats.items():
        if stats['weekly'] == 0:
            continue
        workout_dates = [date.fromisoformat(d) for d in stats['workout_dates']]
        this_week = [d for d in workout_dates if d >= week_start]
        prev = [d for d in workout_dates if d < week_start]
        if prev and this_week:
            gap = (min(this_week) - max(prev)).days
            if gap > 14:
                comebacks.append(person)

    return {
        'most_active_week': {'name': most_active[0], 'count': most_active[1]} if most_active else None,
        'milestones': milestones,
        'comebacks': comebacks,
    }


def compute_mia(person_stats, threshold_days=7):
    mia = [
        {'name': p, 'days_since': s['days_since'], 'last_date': s['last_date']}
        for p, s in person_stats.items()
        if s['days_since'] > threshold_days
    ]
    return sorted(mia, key=lambda x: -x['days_since'])


# ── Output ────────────────────────────────────────────────────────────────────

def generate_stats_json(person_stats, all_months):
    all_dates = [d for s in person_stats.values() for d in s['workout_dates']]
    date_range = {
        'start': min(all_dates) if all_dates else '2026-01-01',
        'end': max(all_dates) if all_dates else date.today().isoformat(),
    }
    return {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'date_range': date_range,
        'people': person_stats,
        'mia': compute_mia(person_stats),
        'spotlight': compute_spotlight(person_stats),
    }


def print_weekly_summary(data, url='[your-site-url]'):
    """Print a WhatsApp-ready weekly summary to stdout."""
    today = date.today()
    week_num = today.isocalendar()[1]
    people = data['people']
    ranked = sorted(people.items(), key=lambda x: -x[1]['total'])
    medals = ['🥇', '🥈', '🥉']

    lines = [
        f'📊 *Accountability Partners — Week {week_num}*',
        f'🔗 {url}',
        '',
        '🏆 *Leaderboard*',
    ]
    for i, (name, s) in enumerate(ranked):
        badge = medals[i] if i < 3 else f'#{i + 1}'
        if s['current_streak'] >= 3:
            streak = f" | 🔥 {s['current_streak']}d"
        elif s['current_streak'] > 0:
            streak = f" | ⚡ {s['current_streak']}d"
        else:
            streak = ''
        lines.append(f"{badge} {s['emoji']} {name} — {s['total']} workouts{streak}")

    spotlight = data['spotlight']
    highlights = []
    if spotlight.get('most_active_week'):
        ma = spotlight['most_active_week']
        e = people.get(ma['name'], {}).get('emoji', '')
        highlights.append(f"Most active: {e} {ma['name']} ({ma['count']} sessions) 💪")
    for m in spotlight.get('milestones', []):
        e = people.get(m['name'], {}).get('emoji', '')
        highlights.append(f"Milestone: {e} {m['name']} crossed #{m['number']}! 🎉")
    for name in spotlight.get('comebacks', []):
        e = people.get(name, {}).get('emoji', '')
        highlights.append(f"Comeback: {e} {name} is back! 🙌")

    if highlights:
        lines += ['', '📅 *This Week*'] + highlights

    mia = [m for m in data.get('mia', []) if m['days_since'] > 7]
    if mia:
        lines += ['', '👀 *Missing in Action*']
        for m in mia[:5]:
            e = people.get(m['name'], {}).get('emoji', '')
            lines.append(f"{e} {m['name']} — {m['days_since']} days 🥱")

    print('\n'.join(lines))
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Parse WhatsApp chat → stats.json')
    parser.add_argument('chat_files', nargs='+', help='One or more _chat.txt files')
    parser.add_argument('--output', default='docs/data/stats.json', help='Output JSON path')
    parser.add_argument('--url', default='[your-site-url]', help='Site URL for weekly summary')
    args = parser.parse_args()

    print(f'Parsing {len(args.chat_files)} file(s)…')
    messages = parse_messages(args.chat_files)
    print(f'  {len(messages)} unique messages')

    print('Extracting workouts…')
    entries = extract_workout_entries(messages)
    for person, e in sorted(entries.items(), key=lambda x: -len(x[1])):
        top = max(item[1] for item in e)
        print(f'  {person}: {len(e)} entries, max #{top}')

    print('Computing stats…')
    person_stats, all_months = build_stats(entries)

    data = generate_stats_json(person_stats, all_months)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f'  Saved → {output_path}')

    print('\n' + '─' * 52)
    print_weekly_summary(data, args.url)


if __name__ == '__main__':
    main()
