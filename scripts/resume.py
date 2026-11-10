#!/usr/bin/env python3
"""SessionStart hook: surface a pending pre-compression handoff in the new context.

The /handoff skill writes the pending handoff to ~/.claude/handoffs/handoff.md
(outside any project tree). This hook runs on SessionStart; when the source is a
compression event (/clear or /compact) and a handoff is pending, it ALWAYS archives
the file once (consume-once), then emits to stdout — which Claude Code injects into
the new context:
  - Fresh handoff (0 <= age <= AUTO_RESUME_MAX_MIN) written in this session's directory
    (the payload `cwd` equals the handoff's `handoff-cwd` stamp): prints the content
    for auto-resume.
  - Anything else (older, future mtime, other or unrecorded directory): emits a prompt
    telling Claude to ASK the user whether to resume from it (default-to-stop — it may
    belong to a different task or session). The file is already archived either way, so
    a leftover can never be SILENTLY re-injected by a later unrelated /clear or /compact.

The skill also drives manual subcommands (any age, no source gate). The first two cover
a fresh session (source=startup) that skipped the hook, so the handoff still lingers:
    python resume.py --consume   # /handoff resume:  archive pending + print its content
    python resume.py --discard   # /handoff discard: archive pending, nothing to resume
    python resume.py --publish   # /handoff: stamp cwd into handoff.md.tmp, move it to pending
    python resume.py --park      # /handoff park: move handoff.md.tmp straight to an archive name

Design notes:
- Source-gated: only /clear and /compact auto-act. Other starts (startup/resume) and
  any missing/unparseable SessionStart payload SKIP — fail closed. The manual
  subcommands exist precisely because a fresh startup session is NOT source-gated.
- Consume-once ALWAYS: the os.replace to the archive is the atomic claim, and it runs
  BEFORE the auto-resume/ask decision — so a pending file is never left behind to be
  silently injected by an unrelated later compression. If the replace fails (a
  concurrent hook already moved it, or it vanished) the run emits nothing.
- Fixed pending path (not per-session): /clear may start a new session, so the hook
  must look somewhere session-independent. Because it is shared by every session, the
  cwd stamp is what keeps one project's /clear from auto-resuming another's handoff.
- Publish is a rename of a fully written tmp that refuses to replace a pending file, so
  the hook never reads a half-written handoff and a failed archive never becomes an
  overwrite.
- Best-effort, never blocks, always exits 0 in hook mode: the hook fires on every start,
  so the whole body is guarded and stdin is only read when piped. --publish and --park
  exit nonzero on refusal so the skill can stop.

    python resume.py --selftest   # dispatch / cwd gate / publish / park / consume / discard
"""
import json
import os
import sys
import time
from datetime import datetime

HANDOFFS = os.path.normpath(os.path.expanduser("~/.claude/handoffs"))
PENDING = os.path.join(HANDOFFS, "handoff.md")
PENDING_TMP = PENDING + ".tmp"
AUTO_RESUME_MAX_MIN = 10                  # <= this (minutes): auto-resume; older: ask the user first
CONSUME_SOURCES = {"clear", "compact"}
CWD_MARK, CWD_MARK_END = "<!-- handoff-cwd: ", " -->"
# Measured 2026-11-10: the desktop app's /clear starts no SessionStart, only /compact does.
DESKTOP_NOTE = ("Desktop app: /clear does not run the SessionStart hook here — "
                "recommend /compact, or /handoff resume after /clear.")


def should_consume(source):
    """Act only on an explicit /clear or /compact SessionStart; skip otherwise."""
    return source in CONSUME_SOURCES


def _read_payload(raw):
    """Best-effort (source, cwd) from the hook's stdin JSON bytes; None for each unknown.

    Takes bytes because json.loads detects UTF-8 itself; text stdin would decode with the
    locale codec (cp949 etc. on Windows) and mangle a non-ASCII cwd."""
    try:
        payload = json.loads(raw)
    except ValueError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    cwd = payload.get("cwd")
    return payload.get("source"), cwd if isinstance(cwd, str) and cwd else None


def _recorded_cwd(content):
    """The directory --publish stamped on the handoff's first line, or None."""
    first = content.split("\n", 1)[0].strip()
    if first.startswith(CWD_MARK) and first.endswith(CWD_MARK_END):
        return first[len(CWD_MARK):-len(CWD_MARK_END)].strip() or None
    return None


def _same_dir(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _ask_reason(age_min, recorded, cwd):
    """Why a pending handoff must not auto-resume, or None when it may."""
    if age_min < 0:
        return "its timestamp is in the future (clock skew)"
    if age_min > AUTO_RESUME_MAX_MIN:
        return f"it was written ~{_ago(age_min)} ago, past the {AUTO_RESUME_MAX_MIN}-min auto-resume window"
    if not recorded:
        return "it has no recorded directory"
    if not cwd:
        return "this session reported no directory"
    if not _same_dir(recorded, cwd):
        return f"it was written in {recorded}, but this session is in {cwd}"
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


def pending_message(pending=PENDING, now=None, cwd=None):
    """Stdout to inject for a pending handoff on a /clear or /compact start, or None.

    `cwd` is the SessionStart payload's directory (None if absent). ALWAYS archives the
    pending file once (consume-once) before deciding, so a leftover can never be silently
    re-injected by a later unrelated compression. Then:
      fresh (0 <= age <= AUTO_RESUME_MAX_MIN) and stamped with this `cwd`: return its
      content for auto-resume.
      otherwise: return a prompt telling Claude to ASK the user first (default-to-stop),
      with the reason — the file is already archived, so the prompt points there."""
    if os.path.islink(pending):
        return None                      # never follow a planted symlink
    try:
        age_min = ((now if now is not None else time.time()) - os.path.getmtime(pending)) / 60
    except OSError:
        return None                      # no pending handoff
    try:
        with open(pending, encoding="utf-8-sig") as f:
            content = f.read()
    except (OSError, UnicodeError):
        return None                      # unreadable / undecodable
    if not content.strip():
        return None                      # empty handoff — nothing to restore, nothing to inject
    archive = _archive(pending, content)  # consume-once, ALWAYS, before the age decision
    if archive is None:
        return None                      # someone else claimed it / it vanished — don't emit
    reason = _ask_reason(age_min, _recorded_cwd(content), cwd)
    if reason is None:
        return ("Resuming from a pre-compression handoff (file archived to "
                f"{archive}; content below):\n\n" + content)
    title = _title(content) or "untitled"
    return (
        f'ACTION REQUIRED — do NOT resume yet. A pre-compression handoff titled "{title}" '
        f"was pending but was not auto-resumed: {reason}. It may belong to a different "
        f"task than the one you are starting. It has been archived to {archive}. Ask the "
        f"user (yes/no): resume from it? Apply it only if they say yes (read that file); "
        f"if no, ignore it — it is already archived, nothing else to do."
    )


def _latest_archive(handoffs):
    """(path, minutes since archived) of the newest timestamp-named archive, or None."""
    try:
        names = os.listdir(handoffs)
    except OSError:
        return None
    stamped = []
    for name in names:
        try:
            stamped.append((datetime.strptime(name[:19], "%Y-%m-%d-%H-%M-%S"), name))
        except ValueError:
            pass                         # not an archive (handoff.md, handoff.md.tmp, ...)
    if not stamped:
        return None
    when, name = max(stamped)
    return os.path.join(handoffs, name), (datetime.now() - when).total_seconds() / 60


def consume_now(pending=PENDING):
    """Manual `/handoff resume`: archive the pending handoff and return its content for
    resume, regardless of age or start source (the user explicitly asked). Otherwise a
    one-line status; with no pending file it names the newest archive, which is where a
    handoff the hook claimed but failed to inject ends up."""
    if os.path.islink(pending):
        return "Refusing to touch a symlinked handoff path."
    try:
        with open(pending, encoding="utf-8-sig") as f:
            content = f.read()
    except FileNotFoundError:
        latest = _latest_archive(os.path.dirname(pending))
        if latest is None:
            return "No pending handoff to resume."
        return (f"No pending handoff to resume. The most recent archived handoff is "
                f"{latest[0]} (archived ~{_ago(latest[1])} ago); if the hook already "
                f"claimed it, read that file to resume.")
    except (OSError, UnicodeError):
        return f"A pending handoff exists at {pending} but is unreadable or not UTF-8; nothing resumed."
    if not content.strip():
        return f"The pending handoff at {pending} is empty; nothing to resume."
    archive = _archive(pending, content)
    if archive is None:
        return "Could not claim the pending handoff (already gone?)."
    return ("Resuming from the pending handoff (archived to "
            f"{archive}; content below):\n\n" + content)


def discard_now(pending=PENDING):
    """Manual `/handoff discard`: archive the pending handoff without resuming (an
    undecodable one too, by bytes), or remove it if empty. Returns a one-line status."""
    if os.path.islink(pending):
        return "Refusing to touch a symlinked handoff path."
    try:
        with open(pending, "rb") as f:
            raw = f.read()
    except OSError:
        return "No pending handoff to discard."
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeError:
        content = None
    if content is not None and not content.strip():
        try:
            os.remove(pending)           # empty: nothing worth preserving
            return "Removed an empty pending handoff."
        except OSError:
            return "No pending handoff to discard."
    archive = _archive(pending, content or "")   # undecodable: no title, stamp-only name
    if archive is None:
        return "Could not discard the handoff (already gone?)."
    kind = "pending handoff" if content is not None else "undecodable (not UTF-8) pending handoff"
    return f"Discarded the {kind} (archived to {archive})."


def _read_tmp(tmp):
    """(content, None) from a freshly written handoff.md.tmp, or (None, refusal message)."""
    try:
        with open(tmp, encoding="utf-8-sig") as f:
            content = f.read()
    except FileNotFoundError:
        return None, f"No handoff at {tmp}; write it there first."
    except (OSError, UnicodeError):
        return None, f"Refusing: {tmp} is unreadable or not UTF-8."
    if not content.strip():
        return None, f"Refusing: {tmp} is empty."
    return content, None


def publish_now(tmp=PENDING_TMP, pending=PENDING, entrypoint=None):
    """`/handoff` step 2: stamp the current directory on the tmp handoff and atomically
    move it to the pending path. Returns (exit code, status); on refusal the tmp file is
    left in place. `entrypoint` defaults to $CLAUDE_CODE_ENTRYPOINT; for the desktop app
    the status adds DESKTOP_NOTE."""
    if entrypoint is None:
        entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    content, refusal = _read_tmp(tmp)
    if refusal:
        return 1, refusal
    if os.path.lexists(pending):
        return 1, (f"Refusing: a handoff is already pending at {pending}; archive it first "
                   f"(/handoff discard). The new handoff stays at {tmp}.")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"{CWD_MARK}{os.getcwd()}{CWD_MARK_END}\n{content}")
        os.replace(tmp, pending)
    except OSError as e:
        return 1, f"Could not publish {tmp}: {e}"
    status = f"Published the handoff to {pending}."
    return 0, status + (" " + DESKTOP_NOTE if entrypoint == "claude-desktop" else "")


def park_now(tmp=PENDING_TMP):
    """`/handoff park`: move the tmp handoff straight to an archive name, never pending.
    Returns (exit code, one-line status)."""
    content, refusal = _read_tmp(tmp)
    if refusal:
        return 1, refusal
    archive = _archive(tmp, content)
    if archive is None:
        return 1, f"Could not park {tmp} (already gone?)."
    return 0, f"Parked the handoff at {archive}."


def emit(text, out=None):
    """Write to stdout (or `out`) as UTF-8 bytes so a non-UTF-8 console can't raise."""
    stream = sys.stdout.buffer if out is None else out
    stream.write(text.encode("utf-8"))
    stream.flush()


def main():
    if "--consume" in sys.argv:          # /handoff resume — manual, any age, no source gate
        emit(consume_now())
        return 0
    if "--discard" in sys.argv:          # /handoff discard — manual archive / cleanup
        emit(discard_now() or "No pending handoff to discard.")
        return 0
    for flag, action in (("--publish", publish_now), ("--park", park_now)):
        if flag in sys.argv:
            code, status = action()
            emit(status)
            return code
    source = cwd = None
    if not sys.stdin.isatty():           # piped JSON in the hook; a tty = manual run, don't block
        source, cwd = _read_payload(sys.stdin.buffer.read())
    if should_consume(source):
        text = pending_message(cwd=cwd)
        if text:
            emit(text)
    return 0


def _selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        symlink_tested = _selftest_in(d)
    print("selftest ok" + ("" if symlink_tested else " (symlink guard skipped: os.symlink unavailable)"))


def _selftest_in(d):
    import io
    import subprocess
    # source dispatch
    assert should_consume("clear") and should_consume("compact")
    assert not should_consume("startup") and not should_consume("resume")
    assert not should_consume(None) and not should_consume("")
    captured = (rb'{"session_id":"s","transcript_path":"t","cwd":"D:\\repository\\handoff",'
                rb'"hook_event_name":"SessionStart","source":"startup"}')
    assert _read_payload(captured) == ("startup", "D:\\repository\\handoff"), "captured payload -> source, cwd"
    assert _read_payload(b"not json") == (None, None) and _read_payload(b"") == (None, None)
    assert _read_payload(b"123") == (None, None), "non-object JSON -> None"
    pending = os.path.join(d, "handoff.md")
    here = f"<!-- handoff-cwd: {d} -->\n"

    def write(text, age_min=0, path=pending):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        if age_min:
            t = time.time() - age_min * 60
            os.utime(path, (t, t))

    def read(path, mode="r"):
        with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as fh:
            return fh.read()

    # fresh + same directory -> auto-resume content + consumed (archived)
    write(here + "# Fix the auth bug ₩\nstate")
    out = pending_message(pending, cwd=d)
    assert out and out.startswith("Resuming") and "# Fix the auth bug" in out, "fresh -> content"
    assert not os.path.exists(pending), "fresh pending consumed (moved)"
    assert any(n.endswith("_fix-the-auth-bug.md") for n in os.listdir(d)), f"archived w/ slug: {os.listdir(d)}"
    # older than the auto window -> ASK prompt, AND consumed (always archive now — no landmine)
    write(here + "# Old abandoned task\nstuff", age_min=AUTO_RESUME_MAX_MIN + 10)
    msg = pending_message(pending, cwd=d)
    assert msg and "ask the user" in msg.lower(), "old -> ASK prompt"
    assert "Old abandoned task" in msg, "ASK names the handoff title"
    assert "archived to" in msg.lower() and "_old-abandoned-task.md" in msg, "ASK embeds the archive path"
    assert not os.path.exists(pending), "old handoff ALSO consumed (no silent-landmine leftover)"
    assert any(n.endswith("_old-abandoned-task.md") for n in os.listdir(d)), "old handoff archived"
    # fresh but not provably this session's directory -> ASK, archived either way
    other = os.path.join(d, "other-project")
    write(here + "# Other project task\nstate")
    msg = pending_message(pending, cwd=other)
    assert msg.startswith("ACTION REQUIRED") and other in msg, "fresh, other cwd -> ASK naming the dirs"
    assert not os.path.exists(pending), "cwd-mismatch handoff still archived"
    write("# Unstamped task\nstate")
    assert pending_message(pending, cwd=d).startswith("ACTION REQUIRED"), "no cwd line -> ASK"
    write(here + "# Payload without cwd\nstate")
    assert pending_message(pending, cwd=None).startswith("ACTION REQUIRED"), "no payload cwd -> ASK"
    write(here + "# Future task\nstate", age_min=-5)
    msg = pending_message(pending, cwd=d)
    assert msg.startswith("ACTION REQUIRED") and "clock skew" in msg, "future mtime -> ASK"
    # a BOM (some editors add one) must not hide the title heading
    with open(pending, "wb") as fh:
        fh.write("﻿# Bom titled task\nstate".encode("utf-8"))
    assert 'titled "Bom titled task"' in pending_message(pending, cwd=d), "BOM title parsed"
    # same task archived twice within a second -> two archives, neither clobbered
    write("# Same title\nfirst")
    a1 = _archive(pending, "# Same title")
    write("# Same title\nsecond")
    a2 = _archive(pending, "# Same title")
    assert a1 != a2 and read(a1).endswith("first") and read(a2).endswith("second"), "_unique keeps both"
    # --publish: refuses over a pending file (tmp kept), else stamps cwd and moves tmp
    tmp = pending + ".tmp"
    write("# Still pending\nold")
    write("# New handoff\nnew", path=tmp)
    code, st = publish_now(tmp, pending)
    assert code == 1 and os.path.exists(tmp) and read(pending).endswith("old"), f"publish refuses: {st}"
    os.remove(pending)
    code, st = publish_now(tmp, pending)
    assert code == 0 and not os.path.exists(tmp), f"publish moves tmp: {st}"
    assert read(pending).split("\n", 1)[0] == f"<!-- handoff-cwd: {os.getcwd()} -->", "publish stamps cwd"
    assert pending_message(pending, cwd=os.getcwd()).startswith("Resuming"), "published handoff resumes in its dir"
    for entry, noted in (("claude-desktop", True), ("cli", False)):
        write("# Entry note\nx", path=tmp)
        code, st = publish_now(tmp, os.path.join(d, f"pending-{entry}.md"), entrypoint=entry)
        assert code == 0 and (DESKTOP_NOTE in st) == noted, f"desktop note only for the app: {entry}: {st}"
    # --park: tmp goes straight to an archive name, never to pending
    write("# Parked task\nstate", path=tmp)
    code, st = park_now(tmp)
    assert code == 0 and not os.path.exists(tmp) and not os.path.exists(pending), f"park: {st}"
    assert "_parked-task.md" in st and read(st.split(" at ", 1)[1].rstrip(".")).endswith("state"), st
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
    with open(pending, "wb") as fh:
        fh.write(b"\xff\xfe not utf8")
    assert "not UTF-8" in consume_now(pending) and os.path.exists(pending), "consume_now: undecodable != none"
    os.remove(pending)
    # no pending -> name the newest archive (where a claimed-but-uninjected handoff lands)
    sub = os.path.join(d, "latest")
    os.mkdir(sub)
    sub_pending = os.path.join(sub, "handoff.md")
    write("# Ancient\nx", path=os.path.join(sub, "2000-01-01-00-00-00_ancient.md"))
    write("# Claimed by the hook\nx", path=sub_pending)
    newest = _archive(sub_pending, "# Claimed by the hook")
    msg = consume_now(sub_pending)
    assert "no pending" in msg.lower() and newest in msg and "ancient" not in msg, f"latest archive named: {msg}"
    # --discard (/handoff discard): archives without resuming; empty -> removed; missing -> message
    write("# Discard me\nstate")
    st = discard_now(pending)
    assert "archived" in st.lower() and not os.path.exists(pending), "discard archives, no content resumed"
    assert any(n.endswith("_discard-me.md") for n in os.listdir(d)), "discard archived w/ slug"
    write("   \n")
    st = discard_now(pending)
    assert "empty" in st.lower() and not os.path.exists(pending), "discard removes empty"
    assert "no pending" in discard_now(pending).lower(), "discard on missing -> message"
    with open(pending, "wb") as fh:
        fh.write(b"\xff\xfe not utf8")
    before = set(os.listdir(d))
    st = discard_now(pending)
    added = set(os.listdir(d)) - before
    assert not os.path.exists(pending) and len(added) == 1 and "undecodable" in st, f"discard archives bytes: {st}"
    assert read(os.path.join(d, added.pop()), "rb") == b"\xff\xfe not utf8", "undecodable bytes preserved"
    # _slug path-safety: no separators survive
    s = _slug("# ../../etc/passwd")
    assert "/" not in s and "\\" not in s and ".." not in s, f"slug path-safe: {s}"
    # emit round-trips non-ASCII as raw utf-8 bytes (a cp949 print would raise)
    buf = io.BytesIO()
    emit("₩ 한글", buf)
    assert buf.getvalue() == "₩ 한글".encode("utf-8"), "emit writes raw utf-8 bytes"
    # main() as the real hook process, against a temp home (expanduser reads USERPROFILE on
    # Windows, HOME elsewhere); a cp949 stdin codec mimics a Korean Windows locale
    home = os.path.join(d, "home")
    hook_pending = os.path.join(home, ".claude", "handoffs", "handoff.md")
    os.makedirs(os.path.dirname(hook_pending))
    env = dict(os.environ, HOME=home, USERPROFILE=home, PYTHONIOENCODING="cp949")

    def hook(payload):
        raw = json.dumps(dict(session_id="s", hook_event_name="SessionStart", **payload), ensure_ascii=False)
        run = subprocess.run([sys.executable, os.path.abspath(__file__)], input=raw.encode("utf-8"),
                             env=env, capture_output=True, timeout=30)
        return run.stdout.decode("utf-8")

    write(here + "# Hook task\nstate", path=hook_pending)
    assert hook({"source": "startup", "cwd": d}) == "" and os.path.exists(hook_pending), "startup -> silent, untouched"
    out = hook({"source": "clear", "cwd": d})
    assert out.startswith("Resuming") and "# Hook task" in out and not os.path.exists(hook_pending), f"clear: {out!r}"
    kdir = os.path.join(d, "한글 프로젝트")
    write(f"<!-- handoff-cwd: {kdir} -->\n# Korean dir task\nstate", path=hook_pending)
    out = hook({"source": "compact", "cwd": kdir})
    assert out.startswith("Resuming") and "# Korean dir task" in out, f"raw UTF-8 payload parsed: {out!r}"
    # a planted symlink at the pending path is never followed or claimed
    target = os.path.join(d, "target.md")
    write(here + "# Planted\nx", path=target)
    link = os.path.join(d, "link.md")
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        return False
    assert pending_message(link, cwd=d) is None and os.path.islink(link), "symlink pending left alone"
    assert read(target).endswith("x"), "symlink target untouched"
    return True


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        code = 0
        try:
            code = main()
        except Exception:
            pass                          # hook fires on every start; never surface an error
        sys.exit(code)                    # nonzero only for a --publish / --park refusal
