---
name: handoff
description: Before the user compresses the context window with /clear or /compact, write a verbatim handoff of the selected critical state a summary would corrupt — exact IDs, cursor, decisions, gotchas, next action — to ~/.claude/handoffs/handoff.md; a SessionStart hook (if installed) re-injects it into the new context and archives it. Invoke ONLY when the user explicitly runs /handoff — never auto-fire. Does NOT clear/compact itself (only the user can).
argument-hint: "[resume|clear]"
---

# /handoff — verbatim state handoff before /clear or /compact

Compressing the context window loses exact detail: `/compact` summarizes (lossy),
`/clear` wipes (total loss). This skill writes a verbatim backstop to disk *first*
so the precise state you choose survives either path — then you can use `/clear`
(no summary call) instead of `/compact` when the handoff is complete.

## Why it exists
- A summary paraphrases: "placed a small order" loses "order 0276917, 293490, 1 share @₩7,600".
- A summary keeps a decision but drops the *why* → relitigation risk.
- The handoff holds only what a summary can't be trusted with, read back verbatim.
  It is **selective, not total** — `/compact` stays the option when you're unsure
  what you left out.
- A complete handoff lets `/clear` replace `/compact`: it skips the summary-generation
  call and keeps every later turn lean (no summary blob lingering in context). Not
  free — `/handoff` is one model turn and re-injecting spends tokens; the win is the
  leaner steady state, not zero cost.

## Steps (run on invocation)

`/handoff` takes an optional verb — dispatch on it first:

### `/handoff` (no argument) — WRITE the handoff (the default, frequent path)
1. **Check for a pending handoff first** — read `~/.claude/handoffs/handoff.md`. If it
   exists and is non-empty, writing overwrites it with no backup: the path is fixed and
   only the consume paths archive. Ask before continuing, quoting its first line:
   > A handoff is already pending: `<its first line>`. Writing a new one overwrites it.
   > Archive it first and continue? (No = I stop; `/handoff resume` recovers it.)

   On yes, run `resume.py --discard` (paths as in `/handoff clear` below), report the
   archive path it prints, then continue. On no, stop — write nothing.
2. **Write `~/.claude/handoffs/handoff.md`** from the template at
   `~/.claude/skills/handoff/handoff.md`. Fill ONLY high-value slots from THIS
   session; omit empty slots. Rule: include a line only if a lossy summary would
   corrupt or drop it. Point to disk state — never transcribe it. Keep it under ~40
   lines. The first line `# <task>` becomes the archive filename.
3. **Tell the user, verbatim:**
   > handoff written. Now compress the window:
   > • `/clear` — no summary call, leaner. Use when the handoff is complete for what's next.
   > • `/compact` — keeps a summary safety net. Use if unsure what you left out.
   >
   > After it, the SessionStart hook (if installed) auto-injects this handoff into the
   > new context and archives the file — I'll resume from it, no action needed. If the
   > hook didn't fire (not installed, or you opened a fresh session instead of
   > `/clear`|`/compact`), run `/handoff resume` to resume from it and archive it.

### `/handoff resume` — resume from a pending handoff in THIS session
Use when a handoff was written but the auto-resume hook did NOT fire — e.g. you started a
fresh session (source=startup) rather than `/clear` or `/compact`, so the hook skipped and
`handoff.md` still lingers.
1. Run the hook in manual mode — it archives the pending handoff (so it can't linger and be
   silently re-injected by a later compression) and prints its content:
   `python "$HOME/.claude/skills/handoff/scripts/resume.py" --consume`
   (Windows: `python "%USERPROFILE%\.claude\skills\handoff\scripts\resume.py" --consume`)
2. Resume the task from the printed content. If it prints "No pending handoff", tell the user.

### `/handoff clear` — discard a pending handoff without resuming
Use to clean up a stale or abandoned pending handoff you do NOT want to resume.
1. Run: `python "$HOME/.claude/skills/handoff/scripts/resume.py" --discard` — it archives the
   pending file (or removes it if empty) and prints a one-line status. Report that status.

## Hard rules
- **User-invoked only** — act on this skill ONLY when the user runs `/handoff`. Never
  auto-fire: it writes a file and tells the user to compress the window.
- **Never run `/clear` or `/compact` yourself** — built-in commands aren't model-invocable.
  Stop after step 2; the user triggers it.
- **`/clear` has no safety net** — recommend it only when the handoff fully covers the
  next step; otherwise `/compact`.
- **Manual-only by nature**: can't protect *auto*-compaction (you can't invoke it before
  an unseen compaction). For precision work, run `/handoff` + `/clear` proactively at
  task boundaries to keep the window under the auto threshold so auto never fires.
- **Write it at the boundary, not ahead**: if you `/handoff` then keep working before
  `/clear`/`/compact`, the injected handoff is stale — re-run `/handoff` just before
  compressing. The hook auto-resumes only a FRESH handoff (≤10 min); an older one is
  archived and the hook ASKS first whether to resume it (default-to-stop, never a silent
  inject). A handoff written but never compressed won't linger dangerously — the next
  `/clear`|`/compact` archives it (and asks), a later `/handoff` asks before overwriting
  it, or clear it now with `/handoff clear`.
- If nothing this session is critical to preserve verbatim (short/routine work), say so and skip it.
