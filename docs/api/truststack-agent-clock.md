# truststack-agent-clock — API

Import name: `agent_clock`. Distribution: `truststack-agent-clock`.

Injects trusted temporal context into LLM prompts so models stop guessing the
date. The main class subclasses `truststack.core.BaseTrustComponent`, giving it
health checks, metrics, structured logging, and (optionally) event emission.

This library has **no database** and performs **no network I/O**. Trusted
external clocks are supported by passing a caller-owned fetcher to
`CallableTimeSource`.

## Public API surface

Exported from `agent_clock` (`__all__`):

`AdapterRegistry`, `AnthropicAdapter`, `CallableTimeSource`, `ClockAdapter`,
`ClockInjector`, `FrozenTimeSource`, `GenericAdapter`, `LangChainAdapter`,
`OpenAIAdapter`, `SystemTimeSource`, `TimeFormat`, `TimeSource`, `TrustedTime`,
`Weekday`, `__version__`, `clock_middleware`, `registry`, `resolve_timezone`.

`__version__ == "0.1.0"`.

## `ClockInjector`

```python
ClockInjector(
    timezone: str | None = None,
    time_source: TimeSource | None = None,
    *,
    event_bus: EventBus | None = None,
    time_format: TimeFormat | None = None,
)
```

- `timezone`: IANA zone name (e.g. `"Asia/Dubai"`). `None` auto-detects the
  system local timezone via `datetime.now().astimezone().tzinfo`.
- `time_source`: source of "now"; defaults to `SystemTimeSource`. Pass a
  `FrozenTimeSource` for deterministic output or a `CallableTimeSource` for a
  trusted external clock.
- `event_bus`: optional `truststack.events.EventBus`; when present, `ainject`
  publishes a `clock.injected` `TrustEvent`.
- `time_format`: presentation options for the time line; defaults to
  `TimeFormat()` (24-hour, no seconds), keeping output byte-identical.

Construction resolves the timezone eagerly (so an unknown zone raises
`ValueError` immediately) and logs a `clock_initialised` record via
`truststack.logging.get_logger`.

Class attributes: `component_name = "agent-clock"`, `component_version = "0.1.0"`.

### Methods & properties

| Member | Signature | Description |
|--------|-----------|-------------|
| `render` | `() -> TrustedTime` | Render the current trusted moment. Increments counter `renders`, sets gauge `last_epoch`. Raises `ValueError` if the source returns a naive datetime. |
| `block` | `(trusted: TrustedTime \| None = None) -> str` | The formatted `Current trusted datetime:` block. Renders a fresh moment when `trusted` is `None`. |
| `inject` | `(prompt: str) -> str` | `block()` + blank line + `User requests:` + `prompt`. Increments `injections`. |
| `ainject` | `async (prompt: str) -> str` | Async variant; traced via `@traced("agent_clock.inject")`; emits a `clock.injected` event when a bus is configured. |
| `version` | `() -> str` | `"0.1.0"` (from `BaseTrustComponent`). |
| `health_check` | `async () -> HealthStatus` | `HEALTHY` unless `render()` raises, otherwise `UNHEALTHY` with the error detail. |
| `metrics` | `async () -> ComponentMetrics` | Counters `renders`, `injections`; gauge `last_epoch`. |
| `timezone` (property) | `-> tzinfo` | The resolved display timezone. |
| `time_format` (property) | `-> TimeFormat` | The active presentation options. |

### Injection output

```
Current trusted datetime:
Wednesday June 10 2026
17:55 +04
Timezone: Asia/Dubai
UTC Offset: +04:00

User requests:
<prompt>
```

The `block()` is exactly the first five lines; `inject()`/`ainject()` append a
blank line, `User requests:`, and the original prompt. The zone abbreviation in
the time line is `datetime.tzname()` for the display zone (e.g. `+04` for
`Asia/Dubai`, `EDT` for `America/New_York`), falling back to `UTC`.

### Events

When an `EventBus` is supplied, `ainject` publishes:

```python
TrustEvent(
    name="clock.injected",
    component="agent-clock",
    data={"utc_iso": <str>, "timezone": <str>},
)
```

`inject` (sync) does **not** emit events.

## Models (`agent_clock.models`)

### `TrustedTime` (frozen Pydantic v2)

An immutable, fully-rendered view of a single trusted moment. Every field is
pre-computed so the same value can be logged, emitted as an event, and embedded
in a prompt without re-deriving formatting.

| Field | Type | Notes |
|-------|------|-------|
| `human_readable` | `str` | `"<date_line>\n<time_line>"`. |
| `date_line` | `str` | e.g. `"Wednesday June 10 2026"`. |
| `time_line` | `str` | e.g. `"17:55 +04"` (clock portion + zone abbreviation). |
| `utc_iso` | `str` | Instant in UTC, ISO-8601. |
| `local_iso` | `str` | Instant in the display zone, ISO-8601. |
| `timezone` | `str` | Zone label (IANA key when available, else abbrev/`UTC±HH:MM`). |
| `utc_offset` | `str` | Signed `+HH:MM`. |
| `weekday` | `Weekday` | StrEnum for the local instant. |
| `abbreviation` | `str` | e.g. `"+04"`, `"EDT"`, `"UTC"`. |
| `epoch` | `float` | POSIX timestamp (seconds since the Unix epoch). |

### `Weekday` (StrEnum)

`MONDAY` … `SUNDAY`, ordered so `Monday == 0` (matches `datetime.weekday()`).
`Weekday.from_datetime(moment)` returns the weekday for a datetime. Values are
the capitalised English names (`"Monday"`, …).

### `TimeFormat` (frozen Pydantic v2)

Presentation options for the rendered time line. Defaults reproduce the
historical byte-identical output, so existing callers are unaffected.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `hour_clock` | `Literal[12, 24]` | `24` | 24-hour, or 12-hour with AM/PM. |
| `show_seconds` | `bool` | `False` | Include `:SS` in the time line. |

- `is_default` (property) — `True` when fields match the historical default
  (`hour_clock == 24 and not show_seconds`).
- `format_time(moment) -> str` — render the bare clock portion (no zone abbrev)
  of a display-zone datetime.

| `TimeFormat(...)` | `format_time` example |
|-------------------|-----------------------|
| `TimeFormat()` | `17:55` |
| `TimeFormat(show_seconds=True)` | `17:55:09` |
| `TimeFormat(hour_clock=12)` | `05:55 PM` |
| `TimeFormat(hour_clock=12, show_seconds=True)` | `05:55:09 PM` |

## Time sources (`agent_clock.sources`)

- `TimeSource` — runtime-checkable Protocol: `now() -> datetime` (must be
  tz-aware).
- `SystemTimeSource` — default; `now()` returns `datetime.now(UTC)`.
- `FrozenTimeSource(fixed: datetime)` — deterministic; rejects naive datetimes
  in `__init__` with `ValueError`. Exposes the `.fixed` property and an
  `.advance(*, seconds: float)` helper that shifts the frozen instant
  forward/backward.
- `CallableTimeSource(fetcher: Callable[[], datetime])` — wraps a user-supplied
  fetcher (e.g. an NTP/HTTPS trusted-time source). The library performs **no**
  network I/O. The fetcher is invoked on every `now()` call. A non-callable
  fetcher raises `TypeError`; a fetcher returning a non-datetime or a naive
  datetime raises `ValueError` — failing toward distrust.
- `resolve_timezone(name: str | None) -> tzinfo` — resolve an IANA name via
  `zoneinfo.ZoneInfo`, or auto-detect local when `None`. Raises
  `ValueError: Unknown timezone: <name>` for an unknown zone.

### Timezone correctness

`render()` normalises the source instant to UTC and converts to the display
zone, so fractional and negative offsets and DST transitions are all correct:

| Zone | Offset | Note |
|------|--------|------|
| `Asia/Kolkata` | `+05:30` | Half-hour offset. |
| `Asia/Kathmandu` | `+05:45` | 45-minute offset. |
| `America/New_York` | `-05:00` / `-04:00` | Negative offset; DST spring-forward handled by `zoneinfo`. |
| `Pacific/Pago_Pago` | `-11:00` | Far-west negative offset; local day can differ from UTC. |
| `UTC` | `+00:00` | Abbreviation `UTC`. |

## Adapters (`agent_clock.adapters`)

All adapters take a `ClockInjector`, implement the runtime-checkable
`ClockAdapter` protocol (`inject(payload) -> payload`), and preserve the input
structure. They are dependency-free: they operate purely on `dict` / `list` /
`str` values, so none of the provider SDKs need be installed.

| Adapter | Accepts | Behaviour |
|---------|---------|-----------|
| `OpenAIAdapter` | `list[dict]` or request `dict` with `"messages"` | Prepend a `system` message; or **merge** the block in front of an existing leading `system` message's content. |
| `AnthropicAdapter` | request `dict` | Prepend to the top-level `"system"` field — string or list of content blocks (`{"type": "text", "text": ...}`); `messages` untouched. |
| `LangChainAdapter` | `str`, `list[tuple[str, str]]`, or `list[dict]` | String routes through `ClockInjector.inject`; tuple list prepends `("system", block)`; dict list prepends a `system` dict. |
| `GenericAdapter` | `str`, `dict`, `list`, or any scalar | Best-effort injection that preserves the payload type (see below). |

`OpenAIAdapter._inject_messages` is a static helper reused by `GenericAdapter`
for `{"messages": ...}` payloads.

### `GenericAdapter` dispatch

| Payload | Result |
|---------|--------|
| `str` | `ClockInjector.inject(payload)` (block + `User requests:`). |
| `dict` with `"messages"` | OpenAI-style prepend/merge of a leading `system` message. |
| `dict` with `"system"` | Anthropic-style prepend to the `system` field. |
| `dict` with `"prompt"` | `{"prompt": "<block>\n\n<existing>"}` (or just the block if empty). |
| `dict` with none of the above | Adds a `"system"` key holding the block. |
| `list` of `dict` | Prepend `{"role": "system", "content": <block>}`. |
| `list` of `tuple` | Prepend `("system", <block>)`. |
| any other `list` | Prepend the block as a leading element. |
| any other scalar | `ClockInjector.inject(str(payload))`. |

Precedence for dicts is `messages` → `system` → `prompt` → fallback.

Optional extras (`[openai]`, `[anthropic]`, `[langchain]`) install the real
provider SDKs; they are needed only by the caller's request-building code, not
by these adapters.

### Adapter registry (`agent_clock.registry`)

`AdapterRegistry` maps a provider name → a factory
`Callable[[ClockInjector], ClockAdapter]`. The module-level singleton `registry`
is pre-populated with `openai`, `anthropic`, `langchain`, and `generic`. Names
are matched case-insensitively (trimmed + lower-cased).

| Member | Signature | Description |
|--------|-----------|-------------|
| `register` | `(name, factory, *, overwrite=False) -> None` | Register a factory. Raises `ValueError` on a blank name, or on a duplicate without `overwrite=True`. |
| `get` | `(name, injector) -> ClockAdapter` | Build the adapter for `injector`. Raises `KeyError` (listing available names) for an unknown name. |
| `available` | `() -> list[str]` | Registered names, sorted alphabetically. |
| `unregister` | `(name) -> None` | Remove a name (no error if absent). |
| `__contains__` | `name in registry` | Case-insensitive membership test. |
| `__iter__` | `iter(registry)` | Iterate the sorted names. |

```python
from agent_clock import ClockInjector, GenericAdapter, registry

clock = ClockInjector(timezone="Asia/Dubai")
registry.available()                       # ['anthropic', 'generic', 'langchain', 'openai']
adapter = registry.get("OpenAI", clock)    # case-insensitive
registry.register("my-llm", lambda c: GenericAdapter(c))
```

## Middleware (`agent_clock.middleware`)

`clock_middleware(injector) -> Callable[[F], F]` returns a decorator that wraps
any sync **or** async LLM-call function and injects trusted time into its first
prompt-shaped argument without changing the signature (uses `functools.wraps`).
It builds one `GenericAdapter` bound to `injector` and reuses it for every call.

Argument selection, in order:

1. The first of the keyword names `prompt` / `messages` / `input` / `text`
   present in `kwargs` whose value is injectable (`str` / `dict` / `list`).
2. Otherwise the first injectable **positional** argument.
3. Otherwise the call passes through unchanged (fails *open* on shape rather
   than corrupting an unrecognised payload).

The selected value is rewritten through the `GenericAdapter`, preserving its
type; all other arguments are forwarded untouched. Coroutine functions are
detected via `inspect.iscoroutinefunction` and wrapped with a matching async
wrapper.

```python
from agent_clock import ClockInjector, clock_middleware

clock = ClockInjector(timezone="Asia/Dubai")

@clock_middleware(clock)
def call_llm(messages: list[dict]) -> list[dict]:
    return messages   # now leads with a trusted-time system message

@clock_middleware(clock)
async def acall_llm(prompt: str) -> str:
    return prompt      # now prefixed with the trusted block
```

## Errors & failure modes

| Condition | Raised |
|-----------|--------|
| Unknown timezone name | `ValueError: Unknown timezone: <name>` |
| `FrozenTimeSource(fixed=...)` with a naive datetime | `ValueError` |
| `CallableTimeSource` fetcher returns a non-datetime | `ValueError` |
| `CallableTimeSource` fetcher returns a naive datetime | `ValueError` |
| `CallableTimeSource` fetcher not callable | `TypeError` |
| `render()` when the source yields a naive datetime | `ValueError: TimeSource.now() must return a timezone-aware datetime` |
| `registry.get` for an unknown provider | `KeyError` (lists available providers) |
| `registry.register` blank name / duplicate without `overwrite` | `ValueError` |
