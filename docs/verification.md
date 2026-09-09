# What was executed

Verified on 9 September 2026 with Python 3.12.14 on macOS 26.6.2. The exact installed
dependency versions are pinned in `requirements.txt`. This record separates
executed behavior from code that still needs real-world verification.

## Automated results

`python -m pytest -q` passed **66 tests**: 16 persistence/workflow tests,
28 handoff/validator tests, 14 editorial-adapter tests, and 8 Streamlit AppTest
scenarios. Machine-readable results are in
[`examples/test-results.xml`](../examples/test-results.xml).

The eight interface scenarios execute `app.py` through Streamlit's real widget
test runtime. They exercise button clicks, radio decisions, approval controls,
database state, validation messages, and download enablement. These are more
than standalone business-logic tests. They do not render a browser page or
perform a browser download.

| Requirement | Executed evidence |
| --- | --- |
| Original wording, punctuation, Unicode and line breaks preserved | Store and handoff tests retain CRLF, blank lines, combining marks, Unicode, and absent final newlines. |
| Accept and reject suggestions | Mixed decisions create a distinct draft, preserve the original and persist decision history. All-rejected review leaves text unchanged. |
| Reject stale, ambiguous and overlapping changes | Store tests check repeated passages, missing matches, overlaps, changed bases, duplicate decisions and rollback after invalid batches. |
| Approval belongs to one revision and hash | Tests reject old approval even for identical-text new revisions. The UI requires a separate explicit approval action. |
| Clean production handoff | CLI generates the actual eight-file ZIP; validator reads actual packaged bytes against external approved source and approval. |
| Missing, duplicated, altered and reordered text | Five parameterized corruption paths alter real candidate data and block export. |
| Wrong revision | CLI and AppTest generate a candidate containing the actual earlier manuscript, then reject it against the current approval. |
| New draft invalidates current handoff | AppTest verifies export remains disabled after editing and after fresh approval, until a fresh candidate passes. |
| Failure and recovery | AppTest verifies missing-line failure, downloadable report, unchanged approved source, rebuilding, revalidation and reopened export. |
| Persistence across app reload | A fresh AppTest instance uses the same SQLite file and retains review decisions, approval, candidate and export state. |
| Safe public default | Independent AppTest sessions receive distinct in-memory stores. Reset clears one session without changing another, and the replacement workspace remains usable. |
| Live mode is fail-closed | A provider key alone cannot expose or call live review. An explicit enable flag, a nonblank key, and per-revision consent are all required. |
| AI unavailable | Missing key, HTTP authentication/rate/server errors, timeouts, refusal, incomplete output and wrong-source output are tested without fallback. |
| Actual SDK request contract | OpenAI SDK 3.10.0 sends the real structured Responses request into a mocked HTTP transport; a synthetic HTTP response is parsed by the real SDK. |
| Manifest is insufficient on its own | Tests alter actual packaged text and recompute manifest hashes; independent text comparison still rejects it. Tests also reject forged success reports, changed approvals and duplicate ZIP entries. |
| Report belongs to current work | Regression assertions prevent an old green pass appearing as the current Handoff result after a new draft. |

## Reproduce the saved demonstration

From the repository root:

```bash
python scripts/run_demo.py --output artifacts/demo
python scripts/verify_package.py artifacts/demo/clean_handoff.zip \
  --source artifacts/demo/intended_revision.json \
  --approval artifacts/demo/approval.json \
  --candidate artifacts/demo/clean_candidate.json
python scripts/smoke_server.py
```

Use a new output directory for subsequent demo runs. Existing evidence is never
silently replaced. UUIDs and timestamps will differ, but the checked behavior
must remain the same. Re-exporting an unchanged candidate produces identical ZIP
bytes, as covered by a test.

The included [`examples/output/evidence.json`](../examples/output/evidence.json)
records the CLI execution: original preservation; persisted accept/reject
decisions; clean candidate and package passing; all five corruptions rejected
with export blocked; recovery passing; and a new draft blocked without fresh
approval. The CLI uses an explicitly named **scripted test reviewer**, so its
approval fixture is not evidence that Salim or another human approved the text.

The real HTTP server smoke check passed: root, health endpoint and frontend
JavaScript asset returned HTTP 200. See
[`examples/server-smoke.json`](../examples/server-smoke.json).

## Browser results

A real-browser walkthrough completed the recorded path from sample manuscript
through editorial decisions, approval, candidate build, validation, and final
ZIP download. It then removed a manuscript line, observed the blocked export,
rebuilt from the approved revision, and revalidated successfully. Tab selection
remained stable across action-triggered reruns, the page header did not cover the
prototype label at desktop or mobile viewport widths, and the browser console
reported no application errors. The checked state is shown in the
[`handoff-ready.jpg`](assets/handoff-ready.jpg) screenshot.

## What remains unverified

- **Live model call:** No live API key was available and no provider request was
  made. The optional integration is implemented and tested with mocked HTTP
  responses. Actual account/model access, model usefulness and latency remain
  unverified.
- **Cross-platform setup:** macOS was tested locally. Linux is exercised by CI,
  but Windows installation and browser behavior remain untested.
- **Enterprise or publishing assurance:** Local approvals are unauthenticated;
  a database administrator can alter local trust records. No authenticated
  reviewer identity, tamper-proof audit store, hosted public deployment, customer
  adoption, measured business outcome, editorial certification or print-ready
  product is claimed.

## Implementation and evidence boundaries

Salim supplied the product problem, workflow requirements, controls and scope in
the project brief. AI assisted implementation, sample/fixture writing, tests and
documentation. The running application has one optional AI provider; it does
not use a multi-agent runtime. Existing private publishing material informed
source-control concepts only and is excluded from this repository.

`examples/source-files-sha256.json` identifies the application, test, fixture,
configuration and script bytes used for this delivery. It is a reproducibility
aid, not a digital signature or protection against rewriting the evidence.
