"""CloudInsight AI - Jinja filters for showing saved timestamps.

Timestamps are stored as naive UTC. The server renders a readable UTC fallback and also emits the
ISO value so the browser (static/js/main.js, `.js-local`) can show it in the viewer's own time zone.
"""


def utc_iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def d_date(dt):
    return dt.strftime("%d %B %Y") if dt else "—"


def d_time(dt):
    return dt.strftime("%I:%M %p") if dt else "—"


def register(app):
    app.add_template_filter(utc_iso, "utc_iso")
    app.add_template_filter(d_date, "d_date")
    app.add_template_filter(d_time, "d_time")
