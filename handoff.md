<!--
Pre-compression handoff TEMPLATE. The /handoff skill fills a copy of this into
~/.claude/handoffs/handoff.md, then you run /clear (no summary call) or /compact
(keeps a summary safety net). Fill ONLY slots holding content a lossy summary
would corrupt or drop. Omit empty slots. Point to disk state — never transcribe
it. Keep the filled file under ~40 lines. The first line `# <task>` becomes the
archive filename slug, so make it a concise task description.
-->
# <task in one line>
> After a manual /clear or /compact, the SessionStart hook (if installed) auto-injects this and archives it when under 24h old; if older, it asks you first whether to use it. On resume, open the Pointers' real files to verify before continuing.

## Cursor — where I am
- In progress: <step N/M, what is half-done>
- Next action: <the very next move>

## Exact values   (a summary rounds these off)
- <order/ID · file:line · param · amount · hash = exact value>

## Environment   (a summary never captures this)
- <branch · dirty/clean · HEAD hash · running server:port · which sub-project>

## Decisions + why   (a summary keeps the choice, drops the reason)
- <chosen> ← <reason / rejected alternative>

## Open / unverified
- <failing test · open question · unverified assumption>

## Gotchas found this session
- <session-specific trap>

## Pointers   (point, don't transcribe)
- plan: <path> · task #N in-progress · touched: <path:line>
