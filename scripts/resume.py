#!/usr/bin/env python3
"""SessionStart hook: surface a pending pre-compression handoff in the new context.

The /handoff skill writes the pending handoff to ~/.claude/handoffs/handoff.md
(outside any project tree). This hook runs on SessionStart; when the source is a
compression event (/clear or /compact) and a handoff is pending, it ALWAYS archives
the file once (consume-once), then emits to stdout — which Claude Code injects into
the new context:
  - Fresh handoff (<= AUTO_RESUME_MAX_MIN): prints the content for auto-resume.
  - Older handoff: emits a prompt telling Claude to ASK the user whether to resume
    from it (default-to-stop — it may belong to a different task). The file is already
    archived either way, so a leftover can never be SILENTLY re-injected by a later
    unrelated /clear or /compact.

The skill also drives two manual subcommands (any age, no source gate) — for the case
where a fresh session (source=startup) skipped the hook, so the handoff still lingers:
    python resume.py --consume   # /handoff resume: archive pending + print its content
    python resume.py --discard   # /handoff clear:  archive pending, nothing to resume

Design notes:
- Source-gated: only /clear and /compact auto-act. Other starts (startup/resume) and
  any missing/unparseable SessionStart payload SKIP — fail closed. The manual
  subcommands exist precisely because a fresh startup session is NOT source-gated.
- Consume-once ALWAYS: the os.replace to the archive is the atomic claim, and it runs
  BEFORE the auto-resume/ask decision — so a pending file is never left behind to be
  silently injected by an unrelated later compression. If the replace fails (a
  concurrent hook already moved it, or it vanished) the run emits nothing.
- Fixed pending path (not per-session): /clear may start a new session, so the hook
  must look somewhere session-independent.
- Best-effort, never blocks, always exits 0: the hook fires on every start, so the
  whole body is guarded and stdin is only read when piped.

    python resume.py --selftest   # dispatch / fresh / old / consume / discard / slug logic
"""
import json
import os
import sys
import time
from datetime import datetime

HANDOFFS = os.path.normpath(os.path.expanduser("~/.claude/handoffs"))
PENDING = os.path.join(HANDOFFS, "handoff.md")
AUTO_RESUME_MAX_MIN = 10                  # <= this (minutes): auto-resume; older: ask the user first
CONSUME_SOURCES = {"clear", "compact"}


def should_consume(source):
    """Act only on an explicit /clear or /compact SessionStart; skip otherwise."""
    return source in CONSUME_SOURCES


def _read_source(stdin_text):
    """Best-effort SessionStart `source` from the hook's stdin JSON; None if unknown."""
    try:
        return json.loads(stdin_text).get("source")
    except (ValueError, AttributeError):
        return None


def _title(content):
    """The handoff's first markdown heading text (for naming / the stale prompt)."""
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()
    return ""


def _slug(content):
    """A short filename slug from the handoff's first heading (the task)."""
    slug = "".join(c if c.isalnum() else "-" for c in _title(content).lower())
    return "-".join(p for p in slug.split("-") if p)[:40]


def _unique(path):
    """Avoid clobbering an existing archive (same second + same task heading)."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{base}-{n}{ext}"):
        n += 1
    return f"{base}-{n}{ext}"


def _ago(age_min):
    """Human 'N min/h/d' for the ask prompt (age is > the auto window, so can be days)."""
    if age_min < 90:
        return f"{int(age_min)} min"
    if age_min < 48 * 60:
        return f"{int(age_min / 60)} h"
    return f"{int(age_min / 1440)} d"


def _archive(pending, content):
    """Atomically move the pending handoff to a timestamped archive path; return that
    path, or None if the move fails (a concurrent claim won, or the file vanished)."""
    stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    slug = _slug(content)
    archive = _unique(os.path.join(os.path.dirname(pending), f"{stamp}{('_' + slug) if slug else ''}.md"))
    try:
        os.replace(pending, archive)     # atomic claim: the loser of a race fails here
        return archive
    except OSError:
        return None                      # someone else claimed it / it vanished


def pending_message(pending=PENDING, now=None):
    """Stdout to inject for a pending handoff on a /clear or /compact start, or None.

    ALWAYS archives the pending file once (consume-once) before deciding, so a leftover
    can never be silently re-injected by a later unrelated compression. Then:
      fresh (<= AUTO_RESUME_MAX_MIN): return its content for auto-resume.
      older: return a prompt telling Claude to ASK the user first (default-to-stop) —
      the file is already archived, so the prompt points there for a 'yes'."""
    if os.path.islink(pending):
        return None                      # never follow a planted symlink
    try:
        age_min = ((now if now is not None else time.time()) - os.path.getmtime(pending)) / 60
    except OSError:
        return None                      # no pending handoff
    try:
        with open(pending, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeError):
        return None                      # unreadable / undecodable
    if not content.strip():
        return None                      # empty handoff — nothing to restore, nothing to inject
    archive = _archive(pending, content)  # consume-once, ALWAYS, before the age decision
    if archive is None:
        return None                      # someone else claimed it / it vanished — don't emit
    if age_min <= AUTO_RESUME_MAX_MIN:
        return ("Resuming from a pre-compression handoff (file archived to "
                f"{archive}; content below):\n\n" + content)
    title = _title(content) or "untitled"
    return (
        f'ACTION REQUIRED — do NOT resume yet. A pre-compression handoff titled "{title}" '
        f"was pending, written ~{_ago(age_min)} ago, so it may belong to a different "
        f"task than the one you are starting. It has been archived to {archive}. Ask the "
        f"user (yes/no): resume from it? Apply it only if they say yes (read that file); "
        f"if no, ignore it — it is already archived, nothing else to do."
    )


def consume_now(pending=PENDING):
    """Manual `/handoff resume`: archive the pending handoff and return its content for
    resume, regardless of age or start source (the user explicitly asked). None if none."""
    if os.path.islink(pending):
        return None
    try:
        with open(pending, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeError):
        return None                      # missing / unreadable / undecodable
    if not content.strip():
        return None
    archive = _archive(pending, content)
    if archive is None:
        return None
    return ("Resuming from the pending handoff (archived to "
            f"{archive}; content below):\n\n" + content)


def discard_now(pending=PENDING):
    """Manual `/handoff clear`: archive the pending handoff without resuming (or remove it
    if empty/undecodable). Returns a one-line status string for the user."""
    if os.path.islink(pending):
        return "Refusing to touch a symlinked handoff path."
    try:
        with open(pending, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return "No pending handoff to clear."
    except UnicodeError:
        content = ""                     # undecodable: treat as nothing worth archiving
    if content.strip():
        archive = _archive(pending, content)
        return (f"Cleared the pending handoff (archived to {archive})."
                if archive else "Could not clear the handoff (already gone?).")
    try:
        os.remove(pending)               # empty/undecodable: nothing worth preserving
        return "Removed an empty pending handoff."
    except OSError:
        return "No pending handoff to clear."


def emit(text, out=None):
    """Write to stdout (or `out`) as UTF-8 bytes so a non-UTF-8 console can't raise."""
    stream = sys.stdout.buffer if out is None else out
    stream.write(text.encode("utf-8"))
    stream.flush()


def main():
    if "--consume" in sys.argv:          # /handoff resume — manual, any age, no source gate
        emit(consume_now() or "No pending handoff to resume.")
        return
    if "--discard" in sys.argv:          # /handoff clear — manual archive / cleanup
        emit(discard_now() or "No pending handoff to clear.")
        return
    source = None
    if not sys.stdin.isatty():           # piped JSON in the hook; a tty = manual run, don't block
        source = _read_source(sys.stdin.read())
    if should_consume(source):
        text = pending_message()
        if text:
            emit(text)


def _selftest():
    import io
    import tempfile
    # source dispatch
    assert should_consume("clear") and should_consume("compact")
    assert not should_consume("startup") and not should_consume("resume")
    assert not should_consume(None) and not should_consume("")
    assert _read_source('{"source":"clear"}') == "clear"
    assert _read_source("not json") is None and _read_source("") is None
    assert _read_source("123") is None, "non-object JSON -> None"
    d = tempfile.mkdtemp()
    pending = os.path.join(d, "handoff.md")

    def write(text, age_min=0):
        with open(pending, "w", encoding="utf-8") as fh:
            fh.write(text)
        if age_min:
            t = time.time() - age_min * 60
            os.utime(pending, (t, t))

    # fresh -> auto-resume content + consumed (archived)
    write("# Fix the auth bug ₩\nstate")
    out = pending_message(pending)
    assert out and out.startswith("Resuming") and "# Fix the auth bug" in out, "fresh -> content"
    assert not os.path.exists(pending), "fresh pending consumed (moved)"
    assert any(n.endswith("_fix-the-auth-bug.md") for n in os.listdir(d)), f"archived w/ slug: {os.listdir(d)}"
    # older than the auto window -> ASK prompt, AND consumed (always archive now — no landmine)
    write("# Old abandoned task\nstuff", age_min=AUTO_RESUME_MAX_MIN + 10)
    msg = pending_message(pending)
    assert msg and "ask the user" in msg.lower(), "old -> ASK prompt"
    assert "Old abandoned task" in msg, "ASK names the handoff title"
    assert "archived to" in msg.lower() and "_old-abandoned-task.md" in msg, "ASK embeds the archive path"
    assert not os.path.exists(pending), "old handoff ALSO consumed (no silent-landmine leftover)"
    assert any(n.endswith("_old-abandoned-task.md") for n in os.listdir(d)), "old handoff archived"
    # _ago formatting: min under 90, hours under 2 days, days beyond
    assert _ago(20) == "20 min" and _ago(120) == "2 h" and _ago(3 * 1440) == "3 d", "age formatting"
    # missing / empty / undecodable -> None (empty/undecodable NOT consumed, left in place)
    assert pending_message(os.path.join(d, "nope.md")) is None, "missing -> None"
    write("   \n")
    assert pending_message(pending) is None and os.path.exists(pending), "empty -> None, left"
    os.remove(pending)
    with open(pending, "wb") as fh:
        fh.write(b"\xff\xfe not utf8")
    assert pending_message(pending) is None and os.path.exists(pending), "undecodable -> None, left"
    os.remove(pending)
    # --consume (/handoff resume): archives + returns content regardless of age
    write("# Manual resume task\nstate", age_min=120)   # 2h old: hook would ask, consume_now resumes
    out = consume_now(pending)
    assert out and out.startswith("Resuming") and "# Manual resume task" in out, "consume_now -> content"
    assert not os.path.exists(pending), "consume_now archives"
    assert any(n.endswith("_manual-resume-task.md") for n in os.listdir(d)), "consume_now archived w/ slug"
    assert consume_now(pending) is None, "consume_now on missing -> None"
    # --discard (/handoff clear): archives without resuming; empty -> removed; missing -> message
    write("# Discard me\nstate")
    st = discard_now(pending)
    assert "archived" in st.lower() and not os.path.exists(pending), "discard archives, no content resumed"
    assert any(n.endswith("_discard-me.md") for n in os.listdir(d)), "discard archived w/ slug"
    write("   \n")
    st = discard_now(pending)
    assert "empty" in st.lower() and not os.path.exists(pending), "discard removes empty"
    assert "no pending" in discard_now(pending).lower(), "discard on missing -> message"
    # _slug path-safety: no separators survive
    s = _slug("# ../../etc/passwd")
    assert "/" not in s and "\\" not in s and ".." not in s, f"slug path-safe: {s}"
    # emit round-trips non-ASCII as raw utf-8 bytes (a cp949 print would raise)
    buf = io.BytesIO()
    emit("₩ 한글", buf)
    assert buf.getvalue() == "₩ 한글".encode("utf-8"), "emit writes raw utf-8 bytes"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        try:
            main()
        except Exception:
            pass                          # hook fires on every start; never surface an error
        sys.exit(0)                       # stdout already emitted (if any); always succeed
