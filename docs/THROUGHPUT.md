# Output tokens per second on completed replies

**Purpose:** show the observed output throughput of a Codex turn directly before
the account quota in its final Feishu reply.

**Use:** send a normal message to a project bot, Control or attached native Debug.
For example, a completed reply can end with:

```text
Speed (turn avg): 20.0 output tok/s (500 tokens / 25.00 s; includes tools).
Quota (account): 7d: 10% used, 90% left
```

**Calculation:** subtract the cumulative output counter at turn start from the
reported cumulative counter at completion, then divide by Codex turn duration
in seconds. Multiple model calls belong to the same turn. Repeated usage events
do not add tokens again. Input tokens, cached input and earlier conversation
output are excluded. Reasoning tokens are already part of reported output;
they are not added a second time. The count also covers generated tool calls
and formatting; it is not the number of words in the final answer.

This is a **turn average**, not the model's generation-only or hardware decoding
speed. The duration includes model/network latency, reasoning, tools, approvals
and any waiting inside Codex. Relay queueing before dispatch, Git validation and
uploads after Codex completion, and Feishu delivery are outside it. A slow Git
retry cannot lower the saved rate. Subagent or other-bot usage is not summed
into this thread's counter. Mid-turn model changes/reroutes do not turn the
measurement into a benchmark of one model.

**Data:** managed turns observe exact-thread/exact-turn usage notifications and
prefer Codex's reported `durationMs`. If that field is absent, the client uses a
monotonic interval from dispatch to completed notification. Attached native
turns use their pinned transcript's numeric usage and `duration_ms`, or its
timestamped start/completion interval. The transcript can refine a managed
observation before the reply is frozen. At attachment/upgrade, a bounded 8 MiB
read behind the existing cursor seeds numeric state without replaying prompts,
tools, history, watches or replies. It never resets the observer cursor.

**Expected result:** one speed line precedes the existing quota. It uses the
same session and turn as the saved answer. Reported counters and durations are
best-effort telemetry, not a billing statement. A missing starting counter,
missing final usage, counter reset, invalid duration or unsuccessful turn
produces `Speed (turn avg): unavailable (missing per-turn token/timing data).`
The relay does not estimate tokens from characters, divide account quota by
time, infer zero usage from missing data or double-count reasoning.
Missing or failed/disconnected usage also invalidates the cached starting
counter, so a later turn cannot inherit output from unmeasured earlier work.

**Verification:** regressions cover multi-call deltas, duplicate counters,
reasoning, invalid/reset counters, multiple native turns in one observation,
bounded upgrade bootstrap, managed thread/turn isolation, timing, ordering and
stable delivery reconciliation across restarts. Existing session roles and a
fresh bot use the same adapters. A fixture-based check is not a live account or
Feishu pilot.

**Limits:** exact generation-only timing is not available in the observed Codex
protocol; a first partial turn can remain unavailable after an upgrade. Later
fully observed turns become measurable. No extra model call, API key, account
permission, package or model change is required. Commands/progress have no
speed footer; explicit offline adapters without Codex telemetry keep their
existing payload. Shared-turn follow-ups use their existing parent final reply,
not separate speed measurements. Already-attempted replies retain their exact
outbox payload; a late observation cannot edit a sent message or replay Codex.

Sources: [Codex app-server events](https://learn.chatgpt.com/docs/app-server),
[OpenAI output-token counting](https://developers.openai.com/api/docs/guides/token-counting).
