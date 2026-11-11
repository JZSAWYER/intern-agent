# Quota commands and completed Feishu replies

**Purpose:** inspect observed Codex account usage, remaining percentage and reset
timing, or see a quota footer when a conversation finishes a turn.

**Prerequisites:** the Feishu relay is connected to a Codex conversation. Quota
percentages require Codex to report account rate limits; API-key providers may
not expose a ChatGPT quota.

**Use:** send `/usage`, `/cost` or `/quota` in a bot chat or bound Control chat.
All three inspect the same account limits. In management, an explicit session
argument selects that conversation. The response is readable text, for example:

```text
Quota (account): 7d: 50% used, 50% left
Last Codex report: 3s ago.
7d resets in 6d 4h.
```

Reported session-token counts are shown separately. Missing counts are labelled
not reported, and monetary cost is not inferred from quota or tokens. The
command uses an observation at most 30 seconds old when available. Otherwise it
queries the existing backend with one five-second connection/initialization/read
budget and a short close timeout. It never starts or restarts a backend for an
inspection. If refresh fails, an unexpired observation up to 15 minutes old is
clearly labelled with its age; missing/stale limits remain unavailable.
Recording these read-only commands queues history publication in the background,
so the next command does not wait for their Git projection/push. Final model
replies retain their normal confirmed-Git publication gate.

A normal message to a project bot, Control or the attached Debug Bot also ends
its completed reply with one line, for example:

```text
Quota (account): 5h: 25% used, 75% left | 7d: 10% used, 90% left
```

**Expected result:** the footer shows each reported window. A weekly-only account
shows only its `7d` window. Remaining percentage is `100 - usedPercent`; the
windows are not combined. These are account limits shared by conversations
using that account, rather than a per-turn token cost or context-window usage.
When the provider returns several metered buckets, the explicitly identified
`codex` bucket takes precedence over the backward-compatible single-bucket view.
Primary and secondary labels use their actual durations; primary can be weekly.
Management command acknowledgements and progress messages have no quota footer.

Managed turns observe quota notifications and make a read-only refresh on their
existing connection when Codex completes. That refresh has a two-second limit;
a quota failure cannot fail the Codex turn. Attached terminal turns use numeric
quota events from their exact pinned transcript. No new session, credential
transfer or account permission is needed.

An observed [turn-average output speed](THROUGHPUT.md) appears directly before
the quota. It uses per-turn tokens and timing, separately from account quota;
the quota percentages are never used to estimate token throughput.

The footer is saved per reply before sending. An upload/delivery retry preserves
the same footer and message key without rerunning Codex. The owner's prompt and
saved Codex answer remain unchanged. Only sanitized numeric quota windows and
the footer are stored in private relay state; account identities, credit details
and authentication data are excluded.

**Verification:** regression tests cover both windows, weekly-only limits,
remaining-percentage arithmetic, absent/invalid/expired data, bounded refresh,
unchanged prompts, delivery retries across receiver restarts, and distinct quota
snapshots for native turns observed together. The real account endpoint and
native quota-event schema have been checked read-only. Command regressions cover
native/managed routing, short deadlines, failed initialization cleanup, cached
fallback, three command aliases, asynchronous history publication and explicit
Codex-bucket precedence. Automated tests do not
by themselves establish a live Feishu round-trip.
The 331 Python checks passed after updating the existing upload-recovery
expectation for the footer; extension, portable-package and 50 local guide-link
checks also passed.

**Limits:** quota is the latest observed account snapshot, not a guarantee of
unused capacity while other sessions work. A snapshot older than 15 minutes or
a window whose reset has passed is not shown as current. Missing quota produces
`Quota: unavailable (Codex did not report current account limits).` instead of
an invented percentage. Reset credits are never consumed automatically. `/usage`
still provides the existing explicit usage inspection.

An older reply already in flight when the feature is installed retains its
original body during delivery reconciliation. Adding a footer to that attempted
message would change its idempotent payload. New replies freeze their footer
before entering the sending state.
Failed executions with no final response do not freeze a footer for a later
owner-authorized retry.

Source: [official OpenAI app-server quota fields](https://learn.chatgpt.com/docs/app-server).
