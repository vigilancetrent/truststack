# truststack-agent-clock

**Inject trusted temporal context into every LLM call.**

## The problem

LLMs answer with the wrong date because the current date and time are never put
into the prompt. With no "now" to anchor on, the model falls back to its
training cutoff and confidently tells you the wrong year, the wrong weekday, or
that "next Friday" is a date in the past. This breaks scheduling, reminders,
deadline math, "what changed since…" reasoning, and anything that depends on the
present moment.

`truststack-agent-clock` fixes this at the source. It renders a *trusted* "now"
— from the system clock, a frozen instant, or your own NTP/HTTPS time fetcher —
and prepends it to every prompt (or weaves it into a provider-shaped request
payload) so the model never has to guess. It is a
`truststack.core.BaseTrustComponent`, so it ships with health checks, metrics,
structured logging, and optional event emission out of the box.

Design guarantees:

- **Byte-identical by default.** The rendered block is stable; upgrades and new
  features never change the default output. Opt in to a 12-hour clock or seconds
  via `TimeFormat`.
- **No network in the library.** Trusted external clocks (NTP, HTTPS `Date`,
  roughtime) are supported via `CallableTimeSource`, but the *caller* owns the
  transport. The library performs zero I/O.
- **Fails toward distrust.** A naive datetime, a non-datetime fetcher result, or
  an unknown timezone raises a clear `ValueError` rather than silently
  substituting an unverified local clock.

## Install

```bash
uv add truststack-agent-clock
```

Optional provider extras (only needed by *your* client code that builds real
requests — the adapters themselves are dependency-free and operate on plain
dicts/lists/strings):

```bash
uv add "truststack-agent-clock[openai]"      # openai>=1.0
uv add "truststack-agent-clock[anthropic]"   # anthropic>=0.30
uv add "truststack-agent-clock[langchain]"   # langchain-core>=0.2
```

`tzdata` is a **required** dependency so named IANA time zones resolve correctly
on Windows (which ships no system zoneinfo database).

## Usage

```python
from agent_clock import ClockInjector

clock = ClockInjector(timezone="Asia/Dubai")
print(clock.inject("What day is it tomorrow?"))
```

produces (the default, byte-identical block):

```
Current trusted datetime:
Wednesday June 10 2026
17:55 +04
Timezone: Asia/Dubai
UTC Offset: +04:00

User requests:
What day is it tomorrow?
```

Leave `timezone` unset to **auto-detect** the system local zone:

```python
clock = ClockInjector()            # uses the host's local timezone
trusted = clock.render()           # -> TrustedTime model
print(trusted.utc_iso, trusted.utc_offset, trusted.weekday)
```

### Deterministic time for tests

```python
from datetime import UTC, datetime
from agent_clock import ClockInjector, FrozenTimeSource

frozen = FrozenTimeSource(datetime(2026, 6, 10, 13, 55, tzinfo=UTC))
clock = ClockInjector(timezone="Asia/Dubai", time_source=frozen)
assert clock.render().time_line == "17:55 +04"

frozen.advance(seconds=3600)       # move the frozen instant forward one hour
assert clock.render().time_line == "18:55 +04"
```

### Provider adapters

All adapters take a `ClockInjector`, operate on plain dicts/lists/strings, and
**preserve the payload shape** — no SDK required:

```python
from agent_clock import ClockInjector, OpenAIAdapter, AnthropicAdapter, LangChainAdapter

clock = ClockInjector(timezone="Asia/Dubai")

# OpenAI: prepend (or merge into) a leading system message.
OpenAIAdapter(clock).inject([{"role": "user", "content": "hi"}])
# -> [{"role": "system", "content": "<trusted block>"}, {"role": "user", ...}]

# Anthropic: prepend to the top-level `system` field (str or content-block list).
AnthropicAdapter(clock).inject({"system": "Be terse.", "messages": [...]})
# -> {"system": "<trusted block>\n\nBe terse.", "messages": [...]}

# LangChain: string, list[(role, content)] tuples, or list[dict] — returned in kind.
LangChainAdapter(clock).inject("Summarise this.")
# -> "<trusted block>\n\nUser requests:\nSummarise this."
```

When an adapter receives a payload that already has a leading `system` message,
the trusted block is **merged in front** of the existing system content rather
than replacing it.

### Adapter registry & generic injection

Look adapters up by provider name, register your own, or inject into an arbitrary
payload shape with `GenericAdapter`. Names are case-insensitive.

```python
from agent_clock import ClockInjector, GenericAdapter, registry

clock = ClockInjector(timezone="Asia/Dubai")

registry.available()                       # ['anthropic', 'generic', 'langchain', 'openai']
"OpenAI" in registry                       # True (case-insensitive)
adapter = registry.get("openai", clock)    # build the OpenAI adapter for this injector

# GenericAdapter dispatches on payload shape, preserving the type:
generic = registry.get("generic", clock)
generic.inject("Summarise this.")                       # str  -> str (block + "User requests:")
generic.inject({"messages": [...]})                     # OpenAI-style merge
generic.inject({"system": "Be terse."})                 # Anthropic-style prepend
generic.inject({"prompt": "Summarise this."})           # -> {"prompt": "<block>\n\nSummarise this."}
generic.inject({"temperature": 0.2})                    # no known key -> adds {"system": "<block>"}
generic.inject([{"role": "user", "content": "hi"}])     # list[dict]   -> prepend system dict
generic.inject([("user", "hi")])                        # list[tuple]  -> prepend ("system", block)

# Register a custom provider factory (raises ValueError on duplicate w/o overwrite):
registry.register("my-llm", lambda c: GenericAdapter(c))
registry.unregister("my-llm")
```

`registry.get("nope", clock)` raises `KeyError` listing the available providers.

### Middleware (sync **and** async)

`clock_middleware(injector)` returns a decorator that wraps any call function and
injects trusted time into its first prompt-shaped argument, **preserving the
signature** (via `functools.wraps`) and never changing the argument's type. It
detects coroutine functions automatically and returns a matching sync/async
wrapper.

The rewritten argument is the first of the keyword names
`prompt` / `messages` / `input` / `text` found in `kwargs`, otherwise the first
injectable (`str`/`dict`/`list`) positional argument. If no injectable argument
is found, the call passes through unchanged.

```python
from agent_clock import ClockInjector, clock_middleware

clock = ClockInjector(timezone="Asia/Dubai")

@clock_middleware(clock)
def call_llm(messages: list[dict]) -> list[dict]:
    return messages   # already led by a trusted-time system message

@clock_middleware(clock)
async def acall_llm(prompt: str) -> str:
    return prompt      # already prefixed with the trusted block
```

### Custom / NTP trusted-time sources & formats

`CallableTimeSource` is the extension point for *trusted* external clocks. The
library performs **no** network I/O; the caller owns the transport and must
return a timezone-aware datetime (a naive or non-datetime value raises
`ValueError`).

```python
from datetime import datetime
from agent_clock import ClockInjector, CallableTimeSource, TimeFormat

def trusted_now() -> datetime:
    # e.g. query NTP / read an HTTPS `Date` header; must be tz-aware.
    ...

clock = ClockInjector(
    timezone="Asia/Kolkata",
    time_source=CallableTimeSource(trusted_now),
    time_format=TimeFormat(hour_clock=12, show_seconds=True),
)
```

`TimeFormat` controls only the time line; the defaults
(`hour_clock=24, show_seconds=False`) keep the output byte-identical:

| `TimeFormat(...)` | Rendered time line (example) |
|-------------------|------------------------------|
| `TimeFormat()` *(default)* | `17:55 +04` |
| `TimeFormat(show_seconds=True)` | `17:55:09 +04` |
| `TimeFormat(hour_clock=12)` | `05:55 PM +04` |
| `TimeFormat(hour_clock=12, show_seconds=True)` | `05:55:09 PM +04` |

### Timezone edge cases

The renderer is correct for fractional-hour offsets, negative offsets, DST
transitions, and year/day boundaries because it delegates to `zoneinfo`:

| Zone | Offset | Note |
|------|--------|------|
| `Asia/Kolkata` | `+05:30` | Half-hour offset. |
| `Asia/Kathmandu` | `+05:45` | 45-minute offset. |
| `America/New_York` | `-04:00` / `-05:00` | Negative offset; DST spring-forward handled by `zoneinfo`. |
| `Pacific/Pago_Pago` | `-11:00` | Far-west negative offset; day boundary can differ from UTC. |

Unknown zones raise a clear error:

```python
from agent_clock import ClockInjector

ClockInjector(timezone="Mars/Olympus_Mons")
# ValueError: Unknown timezone: 'Mars/Olympus_Mons'
```

A time source that returns a naive datetime is also rejected at render time:

```python
ValueError: TimeSource.now() must return a timezone-aware datetime
```

### Async & events

```python
result = await clock.ainject("Schedule a call for next Friday.")
```

`ainject` is traced via `truststack.observability.traced`. When the injector is
constructed with an `EventBus`, `ainject` publishes a `clock.injected`
`TrustEvent` carrying `{"utc_iso": ..., "timezone": ...}`.

### Trust Stack component contract

`ClockInjector` is a `BaseTrustComponent`:

```python
clock.version()                 # "0.1.0"
await clock.health_check()      # HealthStatus(HEALTHY unless render() raises)
await clock.metrics()           # ComponentMetrics: counters renders/injections, gauge last_epoch
```

## API

| Symbol | Description |
|--------|-------------|
| `ClockInjector(timezone=None, time_source=None, *, event_bus=None, time_format=None)` | Main `BaseTrustComponent`; auto-detects local zone when `timezone` is `None`. |
| `ClockInjector.render() -> TrustedTime` | Render the current trusted moment. |
| `ClockInjector.block(trusted=None) -> str` | The `Current trusted datetime:` block. |
| `ClockInjector.inject(prompt) -> str` | Prepend the block, then `User requests:` + prompt. |
| `ClockInjector.ainject(prompt) -> str` | Async variant; traced; emits a trust event when a bus is set. |
| `ClockInjector.timezone` / `ClockInjector.time_format` | Resolved `tzinfo` / active `TimeFormat`. |
| `ClockInjector.version()/health_check()/metrics()` | Trust Stack component contract. |
| `TrustedTime` | Frozen Pydantic model: `human_readable`, `date_line`, `time_line`, `utc_iso`, `local_iso`, `timezone`, `utc_offset`, `weekday`, `abbreviation`, `epoch`. |
| `Weekday` | `StrEnum` of weekday names (`Monday == 0`); `Weekday.from_datetime(moment)`. |
| `TimeFormat(hour_clock=24, show_seconds=False)` | Frozen presentation config; `.is_default`, `.format_time(moment)`. Defaults keep output byte-identical. |
| `TimeSource` | Protocol — `now() -> datetime` (tz-aware). |
| `SystemTimeSource` | Default; reads the real clock in UTC. |
| `FrozenTimeSource(fixed)` | Deterministic source for tests; `.fixed`, `.advance(seconds=…)`. |
| `CallableTimeSource(fetcher)` | Wraps a user-supplied trusted-time fetcher (NTP/HTTPS); no network in-lib. |
| `resolve_timezone(name \| None) -> tzinfo` | Resolve a zone name, or auto-detect local; `ValueError` for unknown zones. |
| `ClockAdapter` | Protocol — `inject(payload) -> payload`. |
| `OpenAIAdapter`, `AnthropicAdapter`, `LangChainAdapter`, `GenericAdapter` | Concrete payload adapters preserving the input shape. |
| `AdapterRegistry`, `registry` | Name → adapter-factory registry; `register/get/available/unregister`, `in`, `iter`. |
| `clock_middleware(injector)` | Decorator injecting trusted time into a sync/async call's prompt arg. |

See [`docs/api/truststack-agent-clock.md`](../../docs/api/truststack-agent-clock.md)
for the full specification.

---

*Part of the **Trust Stack** — trustworthy building blocks for AI agents.*
