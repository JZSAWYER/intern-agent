# Quota on completed Feishu replies

**Purpose:** see observed Codex account usage and the percentage left when a
conversation finishes a turn.

**Prerequisites:** the Feishu relay is connected to a Codex conversation. Quota
percentages require Codex to report account rate limits; API-key providers may
not expose a ChatGPT quota.

**Use:** send a normal message to a project bot, Control or the attached Debug
Bot. Its completed reply ends with one line, for example:

```text
Quota (account): 5h: 25% used, 75% left | 7d: 10% used, 90% left
```

**Expected result:** the footer shows each reported window. A weekly-only account
shows only its `7d` window. Remaining percentage is `100 - usedPercent`; the
windows are not combined. These are account limits shared by conversations
using that account, rather than a per-turn token cost or context-window usage.
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
native quota-event schema have been checked read-only. Automated tests do not
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
