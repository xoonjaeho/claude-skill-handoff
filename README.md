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

### Commands

| Command | What it does |
|---|---|
| `/handoff` | Write the pre-compression handoff, then you `/clear` or `/compact`. Asks first if one is already pending, and archives it on approval. |
| `/handoff resume` | Resume from a pending handoff **now**, in this session, and archive it — for when you opened a fresh session instead of `/clear`/`/compact`, so the hook never fired. |
| `/handoff park` | Write a handoff for **another** session straight to its archive name (never the pending `handoff.md`) and report the filename; open that session with `resume with "<file>"`. |
| `/handoff discard` | Archive a pending handoff without resuming. |

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
   os.replace() the file to <timestamp>_<task>.md   (atomic claim — ALWAYS, consume-once)
            │
   age ≤ 10 min ?
     ├─ yes → print its content to stdout → injected into new context → auto-resume
     └─ no  → print an ASK prompt (names the archived path) → Claude asks you:
              resume from it, or ignore? (default-to-stop; archived either way)
```

If you open a fresh session instead of `/clear`/`/compact`, the hook is source-gated away
(startup ≠ clear/compact) and the handoff lingers. `/handoff resume` runs that same
archive-and-print path manually (`resume.py --consume`); `/handoff discard` archives it
without resuming (`resume.py --discard`).

Three properties make the hook safe to fire on every start:

- **Source-gated** — only `/clear` and `/compact` auto-act. Plain startup/resume, or a
  missing/unparseable SessionStart payload, skip. Fail closed.
- **Consume-once, always** — on any `/clear`/`/compact` start the `os.replace` to the
  archive runs *before* the age decision, so a pending handoff is claimed exactly once and
  can never be left behind to be silently re-injected by a later unrelated compression. If
  two starts race, the loser's `replace` fails and it emits nothing.
- **Older ≠ silent inject** — only a fresh handoff (≤10 min, i.e. the `/handoff`→compress
  boundary) auto-resumes. An older one is archived and the hook ASKS first (default-to-stop).
  Possibly-mismatched state is never injected silently.

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
- `<YYYY-MM-DD-HH-MM-SS>_<task-slug>.md` — archived handoffs, one per consumed handoff
  (auto-resume, the ask path, or a manual `/handoff resume`|`/handoff discard`).

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
python scripts/resume.py --selftest      # dispatch / fresh / old / consume / discard / slug
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
- **Single pending slot** — one `handoff.md` at a time. `/handoff` asks before overwriting
  a still-pending one and archives it on approval; nothing else backs it up.
- **Write at the boundary, not ahead** — if you `/handoff` then keep working before
  compressing, the injected handoff is stale. Re-run `/handoff` just before `/clear`.

## Dependencies

- Python 3.9+ (stdlib only — no third-party packages)
- A Claude Code build that runs `SessionStart` hooks and injects their stdout into the
  new context (required for auto-resume; the skill's write step works without it)

## License

MIT — see [LICENSE](LICENSE).
