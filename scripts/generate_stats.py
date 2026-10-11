#!/usr/bin/env python3
"""Render the GitHub activity cards in assets/stats from live GitHub data.

Runs in the "Update stats" workflow. Needs GH_TOKEN (any token that can read
public profile data) and GH_LOGIN. Pass --data FILE to render from a saved
JSON payload instead of calling the API.
"""

import argparse
import datetime as dt
import json
import os
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "stats"
API = "https://api.github.com/graphql"

# Markup and stylesheet bytes mostly come from bundled admin templates and
# vendor assets, so they would drown out the languages actually written.
EXCLUDED_LANGUAGES = {"HTML", "CSS", "SCSS", "Sass", "Less"}
TOP_LANGUAGES = 6

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"

THEMES = {
    "dark": {
        "bg": "#0d1117", "border": "#30363d", "text": "#e6edf3", "muted": "#9198a1",
        "subtle": "#6e7681", "grid": "#21262d", "accent": "#3fb950", "bar": "#2ea043",
        "line": "#56d364", "other": "#6e7681",
    },
    "light": {
        "bg": "#ffffff", "border": "#d1d9e0", "text": "#1f2328", "muted": "#59636e",
        "subtle": "#818b98", "grid": "#eff2f5", "accent": "#1a7f37", "bar": "#4ac26b",
        "line": "#1a7f37", "other": "#afb8c1",
    },
}

STYLE = (
    ".fade{animation:fade .7s ease-out backwards}"
    ".grow{transform-box:fill-box;transform-origin:50% 100%;animation:grow .9s cubic-bezier(.2,.7,.2,1) backwards}"
    ".growx{transform-box:fill-box;transform-origin:0 50%;animation:growx 1s cubic-bezier(.2,.7,.2,1) backwards}"
    ".draw{stroke-dasharray:1;animation:draw 1.8s ease-in-out .3s backwards}"
    "@keyframes fade{from{opacity:0;transform:translateY(4px)}}"
    "@keyframes grow{from{transform:scaleY(0)}}"
    "@keyframes growx{from{transform:scaleX(0)}}"
    "@keyframes draw{from{stroke-dashoffset:1}}"
    "@media (prefers-reduced-motion:reduce){.fade,.grow,.growx,.draw{animation:none}}"
)

PROFILE_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    createdAt
    publicRepos: repositories(ownerAffiliations: OWNER, privacy: PUBLIC) { totalCount }
    repositories(ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false, first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { languages(first: 20) { edges { size node { name color } } } }
    }
  }
}
"""

CALENDAR_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalIssueContributions
      restrictedContributionsCount
      contributionCalendar { weeks { contributionDays { date contributionCount } } }
    }
  }
}
"""


def graphql(token, query, variables):
    request = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-stats",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    if body.get("errors"):
        raise SystemExit(f"GraphQL error: {body['errors']}")
    return body["data"]


def fetch(login, token):
    languages, after = {}, None
    while True:
        user = graphql(token, PROFILE_QUERY, {"login": login, "after": after})["user"]
        page = user["repositories"]
        for repo in page["nodes"]:
            for edge in repo["languages"]["edges"]:
                name = edge["node"]["name"]
                entry = languages.setdefault(
                    name, {"name": name, "color": edge["node"]["color"], "size": 0}
                )
                entry["size"] += edge["size"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]

    # A contributions collection spans at most one year, so walk back from
    # today one year at a time until the account creation date.
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    created = dt.datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    days, breakdown, end = {}, None, now
    while end > created:
        start = max(end - dt.timedelta(days=365), created)
        collection = graphql(token, CALENDAR_QUERY, {
            "login": login,
            "from": start.isoformat().replace("+00:00", "Z"),
            "to": end.isoformat().replace("+00:00", "Z"),
        })["user"]["contributionsCollection"]
        if breakdown is None:
            breakdown = {
                "commit": collection["totalCommitContributions"],
                "pull request": collection["totalPullRequestContributions"],
                "review": collection["totalPullRequestReviewContributions"],
                "issue": collection["totalIssueContributions"],
                "private contribution": collection["restrictedContributionsCount"],
            }
        for week in collection["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                if start.date().isoformat() <= day["date"] <= end.date().isoformat():
                    days.setdefault(day["date"], day["contributionCount"])
        end = start

    return {
        "generated": now.date().isoformat(),
        "public_repos": user["publicRepos"]["totalCount"],
        "breakdown": breakdown,
        "days": dict(sorted(days.items())),
        "languages": sorted(languages.values(), key=lambda lang: -lang["size"]),
    }


def summarize(data):
    today = dt.date.fromisoformat(data["generated"])
    counts = {dt.date.fromisoformat(d): c for d, c in data["days"].items()}
    first = min(counts, default=today)

    longest = run = 0
    day = first
    while day <= today:
        run = run + 1 if counts.get(day, 0) else 0
        longest = max(longest, run)
        day += dt.timedelta(days=1)

    # Today still counts toward the streak until it is over.
    current = 0
    day = today if counts.get(today, 0) else today - dt.timedelta(days=1)
    while counts.get(day, 0):
        current += 1
        day -= dt.timedelta(days=1)

    window = [today - dt.timedelta(days=i) for i in range(364, -1, -1)]
    year = [counts.get(d, 0) for d in window]
    average = []
    for d in window:
        week = [counts.get(d - dt.timedelta(days=i), 0) for i in range(7)]
        average.append(sum(week) / 7)

    return {
        "today": today,
        "window": window,
        "year": year,
        "average": average,
        "year_total": sum(year),
        "all_total": sum(counts.values()),
        "active_days": sum(1 for c in year if c),
        "current_streak": current,
        "longest_streak": longest,
        "public_repos": data["public_repos"],
        "breakdown": data["breakdown"],
        "languages": data["languages"],
    }


def plural(n, word):
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def text(x, y, content, size, color, weight="normal", anchor="start", extra=""):
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
        f'fill="{color}" text-anchor="{anchor}"{extra}>{content}</text>'
    )


def card(width, height, label, theme, body):
    t = THEMES[theme]
    label = escape(label, {'"': "&quot;"})
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{label}" font-family="{FONT}">'
        f"<title>{label}</title><style>{STYLE}</style>"
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10" '
        f'fill="{t["bg"]}" stroke="{t["border"]}"/>{body}</svg>\n'
    )


def render_summary(s, theme):
    t = THEMES[theme]
    days = lambda n: "day" if n == 1 else "days"
    tiles = [
        (f"{s['year_total']:,}", "", "12-month total", t["text"]),
        (f"{s['all_total']:,}", "", "All-time total", t["text"]),
        (f"{s['active_days']:,}", "", "Active days", t["text"]),
        (f"{s['current_streak']}", days(s["current_streak"]), "Current streak",
         t["accent"] if s["current_streak"] else t["text"]),
        (f"{s['longest_streak']}", days(s["longest_streak"]), "Longest streak", t["text"]),
        (f"{s['public_repos']}", "", "Public repos", t["text"]),
    ]
    width, height = 860, 104
    step = width / len(tiles)
    parts = []
    for i, (value, unit, label, color) in enumerate(tiles):
        x = step * i + step / 2
        if i:
            parts.append(
                f'<line x1="{step * i:.1f}" y1="24" x2="{step * i:.1f}" y2="80" stroke="{t["grid"]}"/>'
            )
        if unit:
            value += f'<tspan font-size="13" font-weight="normal" fill="{t["muted"]}" dx="4">{unit}</tspan>'
        parts.append(
            f'<g class="fade" style="animation-delay:{i * 0.08:.2f}s">'
            + text(x, 52, value, 26, color, "600", "middle")
            + text(x, 74, label.upper(), 10, t["muted"], "normal", "middle", ' letter-spacing="1.1"')
            + "</g>"
        )
    label = (
        f"{s['year_total']:,} contributions in the last 12 months, {s['all_total']:,} all time, "
        f"{s['active_days']} active days, {s['current_streak']} day current streak, "
        f"{s['longest_streak']} day longest streak, {s['public_repos']} public repositories"
    )
    return card(width, height, label, theme, "".join(parts))


def nice_max(peak):
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if peak <= step * 4:
            return step * 4, step
    return peak, peak / 4


def monotone_path(points):
    """Smooth path through points that never overshoots (Fritsch-Carlson)."""
    n = len(points)
    if n < 2:
        return ""
    dx = [points[i + 1][0] - points[i][0] for i in range(n - 1)]
    slope = [(points[i + 1][1] - points[i][1]) / dx[i] for i in range(n - 1)]
    tangent = [slope[0]] + [
        0 if slope[i - 1] * slope[i] <= 0 else
        3 * (dx[i - 1] + dx[i]) / ((2 * dx[i] + dx[i - 1]) / slope[i - 1] + (dx[i] + 2 * dx[i - 1]) / slope[i])
        for i in range(1, n - 1)
    ] + [slope[-1]]
    path = [f"M{points[0][0]:.1f},{points[0][1]:.1f}"]
    for i in range(n - 1):
        (x0, y0), (x1, y1) = points[i], points[i + 1]
        h = dx[i] / 3
        path.append(
            f"C{x0 + h:.1f},{y0 + tangent[i] * h:.1f} {x1 - h:.1f},{y1 - tangent[i + 1] * h:.1f} {x1:.1f},{y1:.1f}"
        )
    return "".join(path)


def render_activity(s, theme):
    t = THEMES[theme]
    width, height = 860, 268
    left, right, top, bottom = 52, 836, 72, 206
    year, window = s["year"], s["window"]
    step = (right - left) / len(year)
    bar_width = max(step * 0.62, 1)
    peak = max(year)
    y_max, tick = nice_max(max(peak, max(s["average"]), 1))
    y = lambda v: bottom - v / y_max * (bottom - top)
    cx = lambda i: left + i * step + step / 2

    parts = [text(24, 38, "Contribution activity", 15, t["text"], "600")]

    v = tick
    while v <= y_max:
        parts.append(f'<line x1="{left}" y1="{y(v):.1f}" x2="{right}" y2="{y(v):.1f}" stroke="{t["grid"]}"/>')
        parts.append(text(left - 10, y(v) + 3.5, f"{v:g}", 10, t["subtle"], anchor="end"))
        v += tick
    parts.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="{t["border"]}"/>')

    for i, day in enumerate(window):
        if day.day == 1 and cx(i) < right - 30:
            label = day.strftime("%b") if day.month != 1 else day.strftime("%b %Y")
            parts.append(text(cx(i), bottom + 18, label, 10, t["subtle"]))

    bars = []
    for i, count in enumerate(year):
        if count:
            bars.append(
                f'<rect x="{cx(i) - bar_width / 2:.2f}" y="{y(count):.1f}" width="{bar_width:.2f}" '
                f'height="{bottom - y(count):.1f}" fill="{t["bar"]}"/>'
            )
    parts.append(f'<g class="grow" opacity="0.7">{"".join(bars)}</g>')

    line = monotone_path([(cx(i), y(a)) for i, a in enumerate(s["average"])])
    parts.append(
        f'<path class="draw" pathLength="1" d="{line}" fill="none" stroke="{t["line"]}" '
        f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
    )

    if peak:
        i = year.index(peak)
        anchor = "start" if cx(i) < left + 60 else "end" if cx(i) > right - 60 else "middle"
        label = f"{plural(peak, 'contribution')} on {window[i].strftime('%b')} {window[i].day}"
        parts.append(
            f'<g class="fade" style="animation-delay:1.2s">'
            f'<circle cx="{cx(i):.1f}" cy="{y(peak):.1f}" r="3.5" fill="{t["accent"]}" stroke="{t["bg"]}" stroke-width="2"/>'
            + text(cx(i), y(peak) - 10, label, 10, t["muted"], anchor=anchor)
            + "</g>"
        )

    footer = height - 22
    parts.append(
        f'<rect x="24" y="{footer - 9}" width="10" height="10" rx="2" fill="{t["bar"]}" opacity="0.7"/>'
        + text(40, footer, "Daily", 11, t["muted"])
        + f'<line x1="86" y1="{footer - 4}" x2="102" y2="{footer - 4}" stroke="{t["line"]}" stroke-width="2" stroke-linecap="round"/>'
        + text(108, footer, "7-day average", 11, t["muted"])
    )
    breakdown = " · ".join(plural(n, k) for k, n in s["breakdown"].items() if n)
    if breakdown:
        parts.append(text(width / 2 + 40, footer, breakdown, 11, t["muted"], anchor="middle"))
    updated = f"Updated {s['today'].day} {s['today'].strftime('%b %Y')}"
    parts.append(text(width - 24, footer, updated, 11, t["subtle"], anchor="end"))

    label = (
        f"Daily contributions over the last 12 months with a 7-day average, "
        f"peaking at {peak} in one day"
    )
    return card(width, height, label, theme, "".join(parts))


def render_languages(s, theme):
    t = THEMES[theme]
    width, height = 860, 120
    languages = [lang for lang in s["languages"] if lang["name"] not in EXCLUDED_LANGUAGES]
    total = sum(lang["size"] for lang in languages) or 1
    items = [(lang["name"], lang["color"] or t["other"], lang["size"]) for lang in languages[:TOP_LANGUAGES]]
    rest = sum(lang["size"] for lang in languages[TOP_LANGUAGES:])
    if rest:
        items.append(("Other", t["other"], rest))

    parts = [text(24, 38, "Top languages", 15, t["text"], "600")]
    left, right, bar_y = 24, width - 24, 54
    x, segments = left, []
    for name, color, size in items:
        w = size / total * (right - left)
        segments.append(f'<rect x="{x:.2f}" y="{bar_y}" width="{max(w - 2, 0.5):.2f}" height="10" fill="{color}"/>')
        x += w
    parts.append(
        f'<clipPath id="bar"><rect x="{left}" y="{bar_y}" width="{right - left}" height="10" rx="5"/></clipPath>'
        f'<g clip-path="url(#bar)"><g class="growx">{"".join(segments)}</g></g>'
    )

    column = (right - left) / max(len(items), 1)
    for i, (name, color, size) in enumerate(items):
        x = left + column * i
        share = f'<tspan fill="{t["muted"]}" dx="6">{size / total * 100:.1f}%</tspan>'
        parts.append(
            f'<g class="fade" style="animation-delay:{0.3 + i * 0.06:.2f}s">'
            f'<circle cx="{x + 5:.1f}" cy="{bar_y + 36}" r="5" fill="{color}"/>'
            + text(x + 16, bar_y + 40, escape(name) + share, 12, t["text"])
            + "</g>"
        )

    label = "Top languages by code size: " + ", ".join(
        f"{name} {size / total * 100:.1f}%" for name, _, size in items
    )
    return card(width, height, label, theme, "".join(parts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", help="render from a saved JSON payload instead of the API")
    args = parser.parse_args()

    if args.data:
        data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    else:
        token, login = os.environ.get("GH_TOKEN"), os.environ.get("GH_LOGIN")
        if not token or not login:
            raise SystemExit("GH_TOKEN and GH_LOGIN must be set")
        data = fetch(login, token)

    stats = summarize(data)
    for theme in THEMES:
        folder = OUT / theme
        folder.mkdir(parents=True, exist_ok=True)
        for name, render in (
            ("summary", render_summary),
            ("activity", render_activity),
            ("languages", render_languages),
        ):
            (folder / f"{name}.svg").write_text(render(stats, theme), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
