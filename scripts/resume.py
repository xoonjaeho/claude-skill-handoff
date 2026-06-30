#!/usr/bin/env python3
"""SessionStart hook: surface a pending pre-compression handoff in the new context.

The /handoff skill writes the pending handoff to ~/.claude/handoffs/handoff.md
(outside any project tree). This hook runs on SessionStart; when the source is a
compression event (/clear or /compact) and a handoff is pending, it emits to stdout
— which Claude Code injects into the new context:
  - Fresh handoff (<= 24h): prints the content for auto-resume AND atomically claims
    the file by moving it to ~/.claude/handoffs/<timestamp>_<task>.md (the archive).
  - Stale handoff (> 24h): does NOT consume it; instead emits a prompt telling Claude
    to ASK the user whether to read+resume from it or discard it (possibly-stale state
    should not be applied silently). The file is left in place until the user decides.

Design notes:
- Source-gated: only /clear and /compact act. Other starts (startup/resume) and any
  missing/unparseable SessionStart payload SKIP — fail closed.
- The os.replace IS the atomic claim for the fresh path: if it fails (a concurrent
  hook already moved the file, or it vanished) the run emits nothing, so two racing
  starts can't double-inject.
- Fixed pending path (not per-session): /clear may start a new session, so the hook
  must look somewhere session-independent.
- Best-effort, never blocks, always exits 0: the hook fires on every start, so the
  whole body is guarded and stdin is only read when piped.

    python resume.py --selftest   # verify dispatch / fresh / stale / emit / slug logic
"""
import json
import os
import sys
import time
from datetime import datetime

HANDOFFS = os.path.normpath(os.path.expanduser("~/.claude/handoffs"))
PENDING = os.path.join(HANDOFFS, "handoff.md")
MAX_AGE_H = 24                            # <= this: auto-resume; > this: ask the user first
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


def pending_message(pending=PENDING, now=None):
    """Stdout to inject for a pending handoff, or None.

    Fresh (<=24h): archive it and return its content for auto-resume.
    Stale (>24h): leave it and return a prompt telling Claude to ASK the user whether
    to resume from it or discard it (don't auto-apply possibly-stale state)."""
    if os.path.islink(pending):
        return None                      # never follow a planted symlink
    try:
        age_h = ((now if now is not None else time.time()) - os.path.getmtime(pending)) / 3600
    except OSError:
        return None                      # no pending handoff
    try:
        with open(pending, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeError):
        return None                      # unreadable / undecodable
    if not content.strip():
        return None                      # empty handoff — nothing to restore
    if age_h > MAX_AGE_H:                 # stale: ask, don't consume
        title = _title(content) or "untitled"
        return (
            f"ACTION REQUIRED — do NOT resume yet. A pre-compression handoff titled "
            f'"{title}" is pending at {pending}, written ~{int(age_h)}h ago, so it may '
            f"be stale. Ask the user (yes/no): read it and resume, or discard it? Apply "
            f"it only if they say yes; after they answer, remove the file."
        )
    stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    slug = _slug(content)
    archive = _unique(os.path.join(os.path.dirname(pending), f"{stamp}{('_' + slug) if slug else ''}.md"))
    try:
        os.replace(pending, archive)     # atomic claim: the loser of a race fails here
    except OSError:
        return None                      # someone else claimed it / it vanished — don't emit
    return "Resuming from a pre-compression handoff (file archived; content below):\n\n" + content


def emit(text, out=None):
    """Write to stdout (or `out`) as UTF-8 bytes so a non-UTF-8 console can't raise."""
    stream = sys.stdout.buffer if out is None else out
    stream.write(text.encode("utf-8"))
    stream.flush()


def main():
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
    # fresh -> archive + content
    with open(pending, "w", encoding="utf-8") as fh:
        fh.write("# Fix the auth bug ₩\nstate")
    out = pending_message(pending)
    assert out and out.startswith("Resuming") and "# Fix the auth bug" in out, "fresh -> content"
    assert not os.path.exists(pending), "fresh pending claimed (moved)"
    archived = [n for n in os.listdir(d) if n.endswith(".md")]
    assert archived and archived[0].endswith("_fix-the-auth-bug.md"), f"archived w/ slug: {archived}"
    # stale (>24h) -> ASK prompt naming the handoff, file LEFT in place (not consumed)
    with open(pending, "w", encoding="utf-8") as fh:
        fh.write("# Old abandoned task\nstuff")
    stale_t = time.time() - 30 * 3600
    os.utime(pending, (stale_t, stale_t))
    msg = pending_message(pending)
    assert msg and "ask the user" in msg.lower(), "stale -> ASK prompt"
    assert "Old abandoned task" in msg and pending in msg, "ASK names the handoff + path"
    assert os.path.exists(pending), "stale handoff NOT consumed (left for user to decide)"
    os.remove(pending)
    # missing / empty / undecodable -> None
    assert pending_message(os.path.join(d, "nope.md")) is None, "missing -> None"
    with open(pending, "w", encoding="utf-8") as fh:
        fh.write("   \n")
    assert pending_message(pending) is None and os.path.exists(pending), "empty -> None, left"
    os.remove(pending)
    with open(pending, "wb") as fh:
        fh.write(b"\xff\xfe not utf8")
    assert pending_message(pending) is None, "undecodable -> None (no raise)"
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
