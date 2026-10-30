# Recognition
> **Status:** Active
> **Type:** Domain rules
> **Updated:** 2026-10-23

[recognition.ts](recognition.ts) owns Recognition's scan choices, launch and stop guards,
operation evidence, and run query. The [selector](../store/selectors/recognition.ts)
adapts current-context reads for presentation.

- Start next's Prepare and Learn links use `#recognition?run=<encoded run id>`.
- The shared [hash query reader](hashQuery.ts) preserves Runtime's existing decoding.
  Empty, malformed and unsupported Recognition queries open the normal picker.
- On entry, query change or context epoch change, clear the previous selection. Select
  the requested run only after the context is confirmed and its existing runs read
  lists that defined run. Never infer a run from a label or from another context.
- Apply the route selection once per entry and context so polling preserves the
  person's later picker changes. Selection sends no request: readiness checking and
  preparing still require their existing clicks and consent.
