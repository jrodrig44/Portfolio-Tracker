# Changelog

This changelog adopts the product-version history now as release-convention
context. The earlier milestones below were not published GitHub releases; no
historical release dates or tags are implied.

## 2.1.2

- Added structured shutdown-stage events for request detection and
  acknowledgement, protected-operation drain, worker stops, application
  shutdown signaling, and final process exit. Added the application/version
  startup record. Active operation names and elapsed times are recorded
  without portfolio contents or credentials.
- Bounded protected-operation draining, market-worker joins, and research
  worker termination; idle workers can now be woken promptly. Protected
  database writes remain tracked through completion if their caller is
  cancelled. Batched Yahoo price downloads now use an explicit 10-second
  request timeout where the provider API supports it.
- Added distinct launcher outcomes for graceful shutdown, confirmed forced
  termination, startup/runtime failure, and unconfirmed process-tree
  termination. The launcher checks force-termination status and waits for the
  owned process tree to exit.
- The reported multi-day shutdown delay was not reproduced. The changes improve
  observability and enforce bounded shutdown; they do not establish which
  shutdown stage caused the reported delay.

## 2.1.1

- Added the missing 240x240 Apple touch-icon aliases using the existing PNG.
- Added shared application name/version metadata and discreet version display
  in NiceGUI and the Streamlit fallback, including startup logging.
- Narrowed handling of the exact Windows Proactor socket-shutdown
  `ConnectionResetError` (WinError 10054): diagnostic endpoint/protocol evidence
  is retained and only that traceback is omitted. The originating connection
  and underlying reset cause remain unconfirmed; this is a logging change, not
  a connection fix.

## 2.1.0 — historical baseline

Represents the accumulated NiceGUI features and operational improvements before
this patch. This baseline is recorded now for versioning context, not as a
previously published GitHub release.

## 2.0 — historical milestone

NiceGUI production-generation milestone. Recorded now as historical product
context; it was not a previously published GitHub release.

## 1.x — historical generation

Original Streamlit generation. Recorded now as historical product context; it
was not a previously published GitHub release.

## Versioning convention

- **PATCH** (`2.1.x`): compatible fixes and polish.
- **MINOR** (`2.x.0`): compatible new functionality.
- **MAJOR** (`x.0.0`): a deliberate new product generation or breaking
  compatibility changes.

Increment once per intentional release. Do not increment automatically for
each commit or startup.
