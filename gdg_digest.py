#!/usr/bin/env python3
"""
GDG event digest.

Pulls the next 14 days of published events from gdg.community.dev (the same
feed the site's own calendar view uses), keeps:

  * in-person or hybrid events in the Windsor / Detroit / Toronto / Ottawa
    corridor (all Ontario chapters except the far north, plus all Michigan
    chapters), and
  * online (virtual or hybrid) events from anywhere that start at a sane
    hour in Toronto time,

then writes three files:

  docs/index.html   a live web page (served by GitHub Pages)
  docs/digest.md    a Markdown version (used for the email body)
  docs/digest.json  machine readable, also used to flag NEW events on the
                    next run

Run locally with:  python3 gdg_digest.py
"""

import json
import os
import re
import sys
import html
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ----------------------------------------------------------------- settings

SITE = "https://gdg.community.dev"
HOME_TZ = ZoneInfo("America/Toronto")
DAYS_AHEAD = int(os.environ.get("GDG_DAYS_AHEAD", "14"))

# In-person radius. Chapter "state" values seen on the site are full names.
IN_PERSON_STATES = {"Ontario", "ON", "Michigan", "MI"}
# Ontario chapters that are too far north to count as "between Windsor and Ottawa".
EXCLUDE_CITY_RE = re.compile(
    r"thunder bay|sudbury|sault|timmins|north bay|kenora", re.IGNORECASE
)

# Online events: keep those starting between these hours (Toronto time).
ONLINE_EARLIEST_HOUR = int(os.environ.get("GDG_ONLINE_EARLIEST", "8"))    # 8 am
ONLINE_LATEST_HOUR = int(os.environ.get("GDG_ONLINE_LATEST", "21"))       # 9 pm

OUT_DIR = os.environ.get("GDG_OUT_DIR", "docs")

FIELDS = ",".join(
    [
        "id", "title", "chapter", "audience_type", "event_type_title",
        "venue_name", "venue_city", "city", "start_date", "end_date",
        "url", "tags", "description_short",
    ]
)

# ----------------------------------------------------------------- fetching


def fetch_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "gdg-digest/1.0 (personal event digest)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def fetch_events(start, end):
    """All published events between two dates (inclusive), following pagination."""
    params = {
        "fields": FIELDS,
        "status": "Published",
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
    }
    url = f"{SITE}/api/event/?{urllib.parse.urlencode(params)}"
    results = []
    seen_urls = set()
    while url and url not in seen_urls:
        seen_urls.add(url)
        data = fetch_json(url)
        results.extend(data.get("results", []))
        links = data.get("links") or {}
        url = links.get("next")
        if url and url.startswith("/"):
            url = SITE + url
    return results


def linkedin_url(raw, name, company):
    """Turn whatever the speaker typed into a usable LinkedIn link.

    The site stores things like 'in/fceotto', 'fceotto', or a full URL. When
    nothing was given, fall back to a LinkedIn people search for the name.
    """
    raw = (raw or "").strip()
    if raw:
        if raw.startswith("http"):
            return raw, True
        raw = raw.lstrip("/")
        if raw.startswith("www.linkedin.com") or raw.startswith("linkedin.com"):
            return "https://" + raw, True
        if not raw.startswith("in/"):
            raw = "in/" + raw
        return "https://www.linkedin.com/" + raw, True
    q = urllib.parse.quote(f"{name} {company}".strip())
    return f"https://www.linkedin.com/search/results/people/?keywords={q}", False


def fetch_people(event_id):
    """Speakers and hosts for one event, with LinkedIn links."""
    try:
        detail = fetch_json(f"{SITE}/api/event/{event_id}/")
    except Exception as exc:
        print(f"warning: no detail for event {event_id}: {exc}", file=sys.stderr)
        return []
    return _people_from_detail(detail)


def _people_from_detail(detail):
    people, seen = [], set()
    for role, key in (("speaker", "speakers"), ("host", "hosts")):
        for p in detail.get(key) or []:
            name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            company = (p.get("company") or "").strip()
            url, exact = linkedin_url(p.get("personal_linkedin_page"), name, company)
            people.append(
                {
                    "name": name,
                    "role": role,
                    "title": (p.get("title") or "").strip(),
                    "company": company,
                    "linkedin": url,
                    "linkedin_exact": exact,   # False means it is a search link
                    "twitter": (p.get("personal_twitter") or "").strip().lstrip("@"),
                }
            )
    return people


# ---------------------------------------------------------------- filtering


def parse_dt(s):
    # start_date looks like 2026-09-10T18:30:00-04:00
    return datetime.fromisoformat(s)


def chapter_field(ev, key):
    ch = ev.get("chapter") or {}
    return ch.get(key) or ""


def event_place(ev):
    return ev.get("venue_city") or ev.get("city") or chapter_field(ev, "city")


def in_corridor(ev):
    state = chapter_field(ev, "state")
    if state not in IN_PERSON_STATES:
        return False
    for place in (event_place(ev), chapter_field(ev, "city")):
        if EXCLUDE_CITY_RE.search(place or ""):
            return False
    return True


def sane_online_hour(ev):
    local = parse_dt(ev["start_date"]).astimezone(HOME_TZ)
    return ONLINE_EARLIEST_HOUR <= local.hour <= ONLINE_LATEST_HOUR


def simplify(ev, kind):
    start = parse_dt(ev["start_date"]).astimezone(HOME_TZ)
    end = parse_dt(ev["end_date"]).astimezone(HOME_TZ) if ev.get("end_date") else None
    ch = ev.get("chapter") or {}
    place_bits = [ev.get("venue_name"), event_place(ev), ch.get("state"), ch.get("country")]
    return {
        "id": ev["id"],
        "kind": kind,                       # "in_person" or "online"
        "audience_type": ev.get("audience_type"),
        "title": ev.get("title", "").strip(),
        "chapter": ch.get("title", ""),
        "chapter_url": ch.get("url", ""),
        "where": ", ".join(b for b in place_bits if b),
        "country": ch.get("country", ""),
        "start_local": start.isoformat(),
        "end_local": end.isoformat() if end else None,
        "when": start.strftime("%a %b %d, %Y at %-I:%M %p") + " ET",
        "registration": ev.get("event_type_title", ""),
        "tags": ev.get("tags") or [],
        "blurb": (ev.get("description_short") or "").strip(),
        "url": ev.get("url", ""),
    }


def build_digest(raw, previous_ids, people_lookup):
    in_person, online = [], []
    for ev in raw:
        if not ev.get("start_date") or not ev.get("url"):
            continue
        aud = ev.get("audience_type")
        if aud in ("IN_PERSON", "HYBRID") and in_corridor(ev):
            in_person.append(simplify(ev, "in_person"))
        if aud in ("VIRTUAL", "HYBRID") and sane_online_hour(ev):
            online.append(simplify(ev, "online"))
    cache = {}
    for lst in (in_person, online):
        lst.sort(key=lambda e: e["start_local"])
        for e in lst:
            e["new"] = e["id"] not in previous_ids
            if e["id"] not in cache:
                cache[e["id"]] = people_lookup(e["id"])
            e["people"] = cache[e["id"]]
    return in_person, online


def new_events(in_person, online):
    """Every new event once, soonest first, for the section at the top of the page."""
    seen, out = set(), []
    for e in sorted(in_person + online, key=lambda e: e["start_local"]):
        if e["new"] and e["id"] not in seen:
            seen.add(e["id"])
            out.append(e)
    return out


def people_directory(in_person, online):
    """One row per person across the whole digest, for the connect list."""
    by_name = {}
    for e in in_person + online:
        for p in e["people"]:
            row = by_name.setdefault(p["name"].lower(), dict(p, events=[]))
            if all(x["url"] != e["url"] for x in row["events"]):
                row["events"].append({"title": e["title"], "url": e["url"], "when": e["when"]})
    return sorted(by_name.values(), key=lambda r: r["name"].lower())


# ----------------------------------------------------------------- rendering


def person_label(p):
    bits = [p["name"]]
    if p["title"] or p["company"]:
        bits.append(", ".join(b for b in (p["title"], p["company"]) if b))
    return " | ".join(bits)


def render_markdown(in_person, online, generated, window_start, window_end, people):
    def line(e):
        flag = " **NEW**" if e["new"] else ""
        hybrid = " (hybrid, also online)" if e["audience_type"] == "HYBRID" else ""
        out = (
            f"- **{e['when']}**{flag}: [{e['title']}]({e['url']}){hybrid}  \n"
            f"  {e['chapter']} | {e['where']} | {e['registration']}"
        )
        for p in e["people"]:
            kind = "LinkedIn" if p["linkedin_exact"] else "find on LinkedIn"
            out += f"  \n  {p['role']}: {person_label(p)} ([{kind}]({p['linkedin']}))"
        return out

    fresh = new_events(in_person, online)
    md = [
        f"# GDG events, {window_start:%b %d} to {window_end:%b %d}",
        "",
        f"Generated {generated:%a %b %d, %Y %-I:%M %p} ET. "
        f"{len(in_person)} in person nearby, {len(online)} online, {len(fresh)} new since the last check.",
        "",
        "## New since the last check",
        "",
    ]
    md += [line(e) for e in fresh] or ["Nothing new since the last check."]
    md += [
        "",
        "## In person: Ontario (Windsor to Ottawa) and Michigan",
        "",
    ]
    md += [line(e) for e in in_person] or ["Nothing in the window."]
    md += ["", "## Online (starting 8 am to 9 pm ET)", ""]
    md += [line(e) for e in online] or ["Nothing in the window."]
    md += ["", "## People to connect with (speakers and hosts)", ""]
    if people:
        for p in people:
            kind = "LinkedIn" if p["linkedin_exact"] else "search LinkedIn"
            evs = "; ".join(f"[{x['title']}]({x['url']})" for x in p["events"])
            md.append(f"- {person_label(p)} ([{kind}]({p['linkedin']})) at {evs}")
    else:
        md.append("No speakers or hosts listed yet. They often get added closer to the date.")
    md += ["", f"Source: {SITE}/events/"]
    return "\n".join(md) + "\n"


def render_html(in_person, online, generated, window_start, window_end, people):
    def esc(s):
        return html.escape(s or "")

    def card(e, show_kind=False):
        new = '<span class="badge new">NEW</span>' if e["new"] else ""
        hyb = '<span class="badge">hybrid</span>' if e["audience_type"] == "HYBRID" else ""
        if show_kind:
            hyb += ' <span class="badge">' + ("in person" if e["kind"] == "in_person" else "online") + '</span>'
        tags = " ".join(f'<span class="tag">{esc(t)}</span>' for t in e["tags"][:6])
        blurb = f'<p class="blurb">{esc(e["blurb"])}</p>' if e["blurb"] else ""
        return f"""
      <article class="card{' is-new' if e['new'] else ''}">
        <div class="when">{esc(e['when'])} {new} {hyb}</div>
        <h3><a href="{esc(e['url'])}" target="_blank" rel="noopener">{esc(e['title'])}</a></h3>
        <div class="meta">{esc(e['chapter'])} &middot; {esc(e['where'])} &middot; {esc(e['registration'])}</div>
        {blurb}
        <div class="tags">{tags}</div>
        {people_list(e['people'])}
        <a class="btn rsvp" data-id="{e['id']}" href="{esc(e['url'])}" target="_blank" rel="noopener">View and RSVP</a><a class="unmark" data-id="{e['id']}" href="#" hidden>unmark</a>
        <a class="btn ghost" href="{esc(e['chapter_url'] or SITE)}" target="_blank" rel="noopener">Chapter page (organizers)</a>
      </article>"""

    def person_link(p):
        label = "LinkedIn" if p["linkedin_exact"] else "find on LinkedIn"
        cls = "" if p["linkedin_exact"] else " search"
        x = f' &middot; <a href="https://x.com/{esc(p["twitter"])}" target="_blank" rel="noopener">X</a>' if p["twitter"] else ""
        return (f'<li><span class="role">{esc(p["role"])}</span> <strong>{esc(p["name"])}</strong>'
                + (f' <span class="muted">{esc(", ".join(b for b in (p["title"], p["company"]) if b))}</span>' if (p["title"] or p["company"]) else "")
                + f' &middot; <a class="li{cls}" href="{esc(p["linkedin"])}" target="_blank" rel="noopener">{label}</a>{x}</li>')

    def people_list(ps):
        if not ps:
            return ""
        return '<ul class="people">' + "".join(person_link(p) for p in ps) + "</ul>"

    def section(title, items, empty):
        body = "".join(card(e) for e in items) or f'<p class="empty">{empty}</p>'
        return f'<section><h2>{title} <span class="count">{len(items)}</span></h2>{body}</section>'

    def new_section(items):
        body = "".join(card(e, show_kind=True) for e in items) or '<p class="empty">Nothing new since the last check. Everything below was already in the previous digest.</p>'
        return (f'<section class="fresh"><h2>New since the last check <span class="count">{len(items)}</span></h2>'
                f'<p class="sub">Added to the site since the previous daily run. They also appear in their usual section below.</p>{body}</section>')

    def directory(people):
        if not people:
            body = '<p class="empty">No speakers or hosts listed yet. They usually get added closer to the date, so check the next refresh.</p>'
        else:
            cards = []
            for p in people:
                who = ", ".join(b for b in (p["title"], p["company"]) if b)
                evs = "".join(
                    f'<li><a href="{esc(x["url"])}" target="_blank" rel="noopener">{esc(x["title"])}</a> <span class="muted">{esc(x["when"])}</span></li>'
                    for x in p["events"]
                )
                li_label = "Open LinkedIn profile" if p["linkedin_exact"] else "Find on LinkedIn"
                li_cls = "btn li-btn" if p["linkedin_exact"] else "btn li-btn search"
                x_btn = f'<a class="btn ghost" href="https://x.com/{esc(p["twitter"])}" target="_blank" rel="noopener">X profile</a>' if p["twitter"] else ""
                cards.append(f"""
      <article class="card person">
        <div class="when">{esc(p["role"].upper())}</div>
        <h3>{esc(p["name"])}</h3>
        {f'<div class="meta">{esc(who)}</div>' if who else ''}
        <div class="sessions-label">Session{'s' if len(p['events']) > 1 else ''}</div>
        <ul class="sessions">{evs}</ul>
        <div class="actions"><a class="{li_cls}" href="{esc(p["linkedin"])}" target="_blank" rel="noopener">{li_label}</a>{x_btn}</div>
      </article>""")
            body = '<div class="people-grid">' + "".join(cards) + "</div>"
        return f'<section><h2>People to connect with <span class="count">{len(people)}</span></h2><p class="sub">Speakers and hosts from the events above. "Find on LinkedIn" opens a search for their name and company when they did not list a profile.</p>{body}</section>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GDG events near me</title>
<style>
  :root {{ --ink:#1f1f1f; --muted:#5f6368; --line:#e3e3e3; --accent:#1a73e8; --new:#188038; --bg:#fafafa; }}
  body {{ margin:0; font:16px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color:var(--ink); background:var(--bg); }}
  main {{ max-width:860px; margin:0 auto; padding:32px 20px 64px; }}
  h1 {{ font-size:28px; margin:0 0 4px; }}
  .sub {{ color:var(--muted); margin:0 0 28px; }}
  h2 {{ font-size:20px; margin:36px 0 12px; border-bottom:2px solid var(--line); padding-bottom:6px; }}
  .count {{ font-size:14px; color:var(--muted); font-weight:normal; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:16px 18px; margin:0 0 12px; }}
  .card.is-new {{ border-color:var(--new); }}
  .fresh {{ background:#e6f4ea; border:2px solid var(--new); border-radius:14px; padding:4px 18px 8px; margin-top:8px; }}
  .fresh h2 {{ color:var(--new); border-bottom-color:var(--new); margin-top:12px; }}
  .fresh .sub {{ margin-bottom:12px; }}
  .fresh .card {{ background:#c8e6c9; border:2px solid var(--new); box-shadow:0 1px 3px rgba(24,128,56,.25); }}
  .fresh .badge {{ background:#fff; }}
  .when {{ font-size:14px; color:var(--muted); font-weight:600; }}
  h3 {{ margin:4px 0 6px; font-size:18px; }}
  h3 a {{ color:var(--ink); text-decoration:none; }}
  h3 a:hover {{ text-decoration:underline; }}
  .meta {{ font-size:14px; color:var(--muted); }}
  .blurb {{ font-size:14px; margin:8px 0 0; }}
  .tags {{ margin:8px 0 10px; }}
  .tag {{ display:inline-block; font-size:12px; background:#f1f3f4; border-radius:999px; padding:2px 10px; margin:0 4px 4px 0; }}
  .badge {{ display:inline-block; font-size:11px; letter-spacing:.04em; border-radius:4px; padding:1px 6px; background:#e8f0fe; color:var(--accent); vertical-align:middle; }}
  .badge.new {{ background:#e6f4ea; color:var(--new); }}
  .btn {{ display:inline-block; font-size:14px; font-weight:600; color:#fff; background:var(--accent); border-radius:6px; padding:6px 14px; text-decoration:none; }}
  .btn.ghost {{ background:#fff; color:var(--accent); border:1px solid var(--accent); margin-left:6px; }}
  .btn.rsvp.viewed {{ background:#9aa0a6; color:#fff; }}
  .unmark {{ font-size:12px; color:var(--muted); margin-left:6px; }}
  .people {{ list-style:none; padding:0; margin:8px 0 12px; font-size:14px; }}
  .people li {{ padding:3px 0; border-top:1px dashed var(--line); }}
  .people .role {{ font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); margin-right:4px; }}
  .people a.li {{ color:#0a66c2; font-weight:600; }}
  .people a.li.search {{ font-weight:normal; }}
  .muted {{ color:var(--muted); }}
  .people-grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(360px, 1fr)); gap:12px; }}
  .card.person {{ margin:0; display:flex; flex-direction:column; }}
  .card.person h3 {{ margin-bottom:2px; }}
  .sessions-label {{ font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); margin-top:10px; }}
  .sessions {{ list-style:none; padding:0; margin:2px 0 12px; font-size:14px; flex:1; }}
  .sessions li {{ padding:3px 0; border-top:1px dashed var(--line); }}
  .sessions li a {{ color:var(--ink); }}
  .actions {{ display:flex; gap:8px; flex-wrap:wrap; }}
  .actions .btn {{ margin:0; }}
  .btn.li-btn {{ background:#0a66c2; }}
  .btn.li-btn.search {{ background:#fff; color:#0a66c2; border:1px solid #0a66c2; }}
  @media (max-width: 480px) {{ .people-grid {{ grid-template-columns:1fr; }} }}
  .empty {{ color:var(--muted); }}
  footer {{ margin-top:40px; font-size:13px; color:var(--muted); }}
</style>
</head>
<body>
<main>
  <h1>GDG events near me</h1>
  <p class="sub">{window_start:%b %d} to {window_end:%b %d} &middot; refreshed {generated:%a %b %d, %-I:%M %p} ET &middot; green box = new since the last check &middot; gray button = you already opened it</p>
  {new_section(new_events(in_person, online))}
  {section("In person: Ontario (Windsor to Ottawa) and Michigan", in_person, "Nothing in this window yet. The site adds events all the time; check back after the next refresh.")}
  {section("Online, starting 8 am to 9 pm ET", online, "Nothing in this window.")}
  {directory(people)}
  <footer>Data: <a href="{SITE}/events/">gdg.community.dev/events</a>. Times converted to Toronto time. Which events you have opened is remembered in this browser only.</footer>
</main>
<script>
(function () {{
  var KEY = "gdg-viewed";
  function load() {{ try {{ return JSON.parse(localStorage.getItem(KEY) || "[]"); }} catch (e) {{ return []; }} }}
  function save(ids) {{ try {{ localStorage.setItem(KEY, JSON.stringify(ids)); }} catch (e) {{}} }}
  function paint() {{
    var ids = load();
    document.querySelectorAll("a.rsvp").forEach(function (a) {{
      var seen = ids.indexOf(a.dataset.id) !== -1;
      a.classList.toggle("viewed", seen);
      a.textContent = seen ? "Viewed" : "View and RSVP";
      var u = a.nextElementSibling;
      if (u && u.classList.contains("unmark")) u.hidden = !seen;
    }});
  }}
  document.addEventListener("click", function (ev) {{
    var a = ev.target.closest("a.rsvp");
    if (a) {{
      var ids = load();
      if (ids.indexOf(a.dataset.id) === -1) {{ ids.push(a.dataset.id); save(ids); }}
      paint();
      return;
    }}
    var u = ev.target.closest("a.unmark");
    if (u) {{
      ev.preventDefault();
      save(load().filter(function (x) {{ return x !== u.dataset.id; }}));
      paint();
    }}
  }});
  paint();
}})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------- main


def main():
    now = datetime.now(HOME_TZ)
    window_start = now.date()
    window_end = window_start + timedelta(days=DAYS_AHEAD)

    os.makedirs(OUT_DIR, exist_ok=True)
    json_path = os.path.join(OUT_DIR, "digest.json")

    previous_ids = set()
    if os.path.exists(json_path):
        try:
            with open(json_path, encoding="utf-8") as f:
                prev = json.load(f)
            previous_ids = {e["id"] for e in prev.get("in_person", []) + prev.get("online", [])}
        except Exception as exc:  # a corrupt previous file should not stop the run
            print(f"warning: could not read previous digest: {exc}", file=sys.stderr)

    fixture = os.environ.get("GDG_FIXTURE")
    if fixture:
        with open(fixture, encoding="utf-8") as f:
            fx = json.load(f)
        raw = fx.get("results", [])
        details = fx.get("details", {})
        people_lookup = lambda eid: _people_from_detail(details.get(str(eid), {}))
    else:
        raw = fetch_events(window_start, window_end)
        people_lookup = fetch_people
    print(f"fetched {len(raw)} events between {window_start} and {window_end}")

    in_person, online = build_digest(raw, previous_ids, people_lookup)
    people = people_directory(in_person, online)
    print(f"kept {len(in_person)} in person, {len(online)} online "
          f"({len(new_events(in_person, online))} new), {len(people)} people")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated": now.isoformat(),
                "window_start": str(window_start),
                "window_end": str(window_end),
                "in_person": in_person,
                "online": online,
                "people": people,
            },
            f, indent=2, ensure_ascii=False,
        )
    with open(os.path.join(OUT_DIR, "digest.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(in_person, online, now, window_start, window_end, people))
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_html(in_person, online, now, window_start, window_end, people))
    with open(os.path.join(OUT_DIR, ".nojekyll"), "w") as f:
        f.write("")


if __name__ == "__main__":
    main()
