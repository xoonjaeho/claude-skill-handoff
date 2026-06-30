# /handoff — verbatim state handoff before /clear or /compact

A Claude Code skill that writes a **verbatim backstop to disk** before you compress
the context window. `/compact` summarizes (lossy); `/clear` wipes (total loss). This
skill saves the precise state a summary would corrupt — exact IDs, cursors, decisions
and their *why* — to `~/.claude/handoffs/handoff.md` *first*, so it survives either
path. A companion `SessionStart` hook then re-injects that file into the next context
and archives it, so the new session resumes with no action from you.

## Quick start

```
/handoff
```

- Run it **at the boundary**, right before you `/clear` or `/compact`.
- It is **user-invoked only** — it never auto-fires (it writes a file and tells you to
  compress the window).
- It does **not** run `/clear` or `/compact` itself — built-in commands aren't
  model-invocable. You trigger the compression.

With the hook installed, the full proactive loop is:

```
/handoff   →   /clear   →   (new context auto-resumes from the handoff)
```

`/clear` is preferred over `/compact` when the handoff fully covers what's next: it
skips the summary-generation call and keeps every later turn lean (no summary blob
lingering in context). Use `/compact` instead if you're unsure what you left out — it
keeps a summary safety net.

## How it works

```
[invocation]   /handoff
   └─ Claude writes ~/.claude/handoffs/handoff.md from the template,
      filling ONLY slots a lossy summary would corrupt (selective, not total)

[you compress]   /clear  (or /compact)
   └─ a new context starts → SessionStart hook fires (scripts/resume.py)

[hook: resume.py]
   source ∈ {clear, compact} ?  ─ no ─→  skip (fail closed)
            │ yes
   pending handoff at ~/.claude/handoffs/handoff.md ?  ─ no ─→  skip
            │ yes
   age ≤ 24h ?
     ├─ yes → os.replace() the file to <timestamp>_<task>.md  (atomic claim)
     │        └─ print its content to stdout → injected into new context → auto-resume
     └─ no  → leave the file; print an ASK prompt → Claude asks you: resume or discard?
```

Three properties make the hook safe to fire on every start:

- **Source-gated** — only `/clear` and `/compact` act. Plain startup/resume, or a
  missing/unparseable SessionStart payload, skip. Fail closed.
- **Consume-once** — the `os.replace` to the archive path *is* the claim. If two starts
  race, the loser's `replace` fails and it emits nothing — no double-injection. The
  hook does not rely on Claude deleting anything.
- **Stale ≠ silent drop** — a handoff older than 24h is not auto-applied; the hook asks
  you first whether to resume from it or discard it. Possibly-stale state is never
  injected silently.

## Files

```
handoff/
├── README.md      this file
├── LICENSE        MIT
├── SKILL.md       skill body Claude follows at runtime (the /handoff steps + hard rules)
├── handoff.md     the handoff TEMPLATE the skill fills a copy of
└── scripts/
    └── resume.py  SessionStart hook — re-injects + archives a pending handoff
```

Runtime state lives outside the skill, at `~/.claude/handoffs/`:

- `handoff.md` — the single pending handoff (fixed path, not per-session: `/clear` may
  start a new session id, so the hook must look somewhere session-independent). Present
  only between a `/handoff` and the next compression.
- `<YYYY-MM-DD-HH-MM-SS>_<task-slug>.md` — archived handoffs, one per consumed resume.

## Install

### 1. Place the skill

Copy or clone this directory to `~/.claude/skills/handoff/`:

```bash
git clone <this-repo> ~/.claude/skills/handoff
```

The directory name `handoff` is significant — it must match the slash command
(`/handoff`).

### 2. Register the SessionStart hook

The skill writes the handoff with or without the hook, but **auto-resume needs the
hook**. Open `~/.claude/settings.json`, find `hooks.SessionStart` (create the key if
missing), and append an entry with **no matcher** (it must fire on every start so it can
gate on `source` itself):

**macOS / Linux:**
```json
{
  "hooks": [
    {
      "type": "command",
      "command": "python \"$HOME/.claude/skills/handoff/scripts/resume.py\""
    }
  ]
}
```

**Windows:**
```json
{
  "hooks": [
    {
      "type": "command",
      "command": "python \"%USERPROFILE%\\.claude\\skills\\handoff\\scripts\\resume.py\""
    }
  ]
}
```

> **Note:** the `\\` sequences are JSON string escapes for a single backslash. If your
> viewer renders them as a single `\`, restore the doubled form when copying into
> `settings.json` — JSON parsers reject single backslashes.

The hook short-circuits to exit 0 unless the start `source` is `clear`/`compact` *and* a
pending handoff exists, so it has no effect on ordinary sessions.

### 3. Verify

Run this from any shell — same command works on bash, zsh, cmd.exe, and PowerShell:

```
python -c "import json,pathlib; p=pathlib.Path.home()/'.claude'/'settings.json'; s=json.loads(p.read_text(encoding='utf-8')); hooks=[h for e in s.get('hooks',{}).get('SessionStart',[]) for h in e.get('hooks',[]) if 'handoff' in h.get('command','')]; print('registered' if hooks else 'NOT registered')"
```

Expect `registered`. Then exercise the hook logic directly:

```
python scripts/resume.py --selftest      # dispatch / fresh / stale / emit / slug
```

Expect `selftest ok`. For a full end-to-end check, run `/handoff` in a session, then
`/clear`, and confirm the new context opens with the handoff content.

### 4. Disable / remove

Either:
- Remove the `SessionStart` entry from `settings.json` (the skill still writes handoffs;
  you resume manually with "read `~/.claude/handoffs/handoff.md` and resume"), or
- Delete `~/.claude/skills/handoff/`.

## Limits / out-of-scope

- **Manual-only by nature** — can't protect against *auto*-compaction (you can't invoke
  `/handoff` before an unseen compaction fires). For precision work, run `/handoff` +
  `/clear` proactively at task boundaries to keep the window under the auto threshold.
- **Selective, not total** — the handoff holds only what a summary can't be trusted
  with. It is not a transcript; `/compact` stays the option when you're unsure what you
  left out.
- **Single pending slot** — one `handoff.md` at a time. Writing a new one before the
  previous is consumed overwrites it.
- **Write at the boundary, not ahead** — if you `/handoff` then keep working before
  compressing, the injected handoff is stale. Re-run `/handoff` just before `/clear`.

## Dependencies

- Python 3.9+ (stdlib only — no third-party packages)
- A Claude Code build that runs `SessionStart` hooks and injects their stdout into the
  new context (required for auto-resume; the skill's write step works without it)

## License

MIT — see [LICENSE](LICENSE).
