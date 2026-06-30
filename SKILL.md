---
name: handoff
description: Before the user compresses the context window with /clear or /compact, write a verbatim handoff of the selected critical state a summary would corrupt — exact IDs, cursor, decisions, gotchas, next action — to ~/.claude/handoffs/handoff.md; a SessionStart hook (if installed) re-injects it into the new context and archives it. Invoke ONLY when the user explicitly runs /handoff — never auto-fire. Does NOT clear/compact itself (only the user can).
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
1. **Write `~/.claude/handoffs/handoff.md`** from the template at
   `~/.claude/skills/handoff/handoff.md`. Fill ONLY high-value slots from THIS
   session; omit empty slots. Rule: include a line only if a lossy summary would
   corrupt or drop it. Point to disk state — never transcribe it. Keep it under ~40
   lines. The first line `# <task>` becomes the archive filename.
2. **Tell the user, verbatim:**
   > handoff written. Now compress the window:
   > • `/clear` — no summary call, leaner. Use when the handoff is complete for what's next.
   > • `/compact` — keeps a summary safety net. Use if unsure what you left out.
   >
   > After it, the SessionStart hook (if installed) auto-injects this handoff into the
   > new context and archives the file — I'll resume from it, no action needed. If the
   > hook isn't installed, say "read ~/.claude/handoffs/handoff.md and resume".

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
  compressing. A handoff older than 24h is not auto-resumed: the hook asks you first
  whether to use it or discard it (no silent drop).
- If nothing this session is critical to preserve verbatim (short/routine work), say so and skip it.
