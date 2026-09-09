# Case study: Manuscript to Handoff

In a publishing workflow, an approved sentence can disappear between editorial review and production. The next file may look plausible while carrying an old revision, a missing line, or an unintended edit. Adding AI makes it more useful to keep the boundary clear: suggestions need judgment; approved text needs reliable transfer.

Salim Ocasio framed this project as a small portfolio application inspired by his publishing-house workflow. His brief required one complete path from manuscript to handoff, explicit human approval, and a visible error that ordinary code actually catches. Source code, fixtures, tests, and documentation were developed with AI assistance. The fictional sample was created for this prototype.

## Product decisions

| Decision | Reason and tradeoff |
| --- | --- |
| Build one local workflow with Streamlit and SQLite | Makes the behavior inspectable and easy to reproduce; leaves multi-user identity and collaboration outside the prototype |
| Separate suggestions, decisions, revisions, and approval | Accepting an edit cannot quietly authorize production; adds one deliberate human step |
| Use AI for optional editorial suggestions | Keeps editorial judgment reviewable; correctness checks remain ordinary code |
| Ship an authored offline fixture alongside a live adapter | Makes the demo usable without credentials; the fixture proves workflow behavior, not live model quality |
| Preserve exact source spans and inspect actual ZIP files | Checks the artifact people receive; a generator's own metadata cannot establish correctness |
| Use four fixed spreads and a text-only brief | Keeps the project focused; leaves professional pagination, illustration, and typography for later work |

Accessible historical material was reviewed briefly: the curated worth-keeping README and `Books_Project_Instructions.md`. Version-aware approval and source checks informed the design. Older or rejected renders and build scripts were excluded and provide no evidence of this prototype's success.

## What the demonstration establishes

The [executed CLI demonstration](../examples/output/evidence.json) accepted one suggestion and rejected another, preserved those decisions after reopening the store, recorded a scripted test approval, and validated the actual clean ZIP. It then rejected missing, duplicated, altered, reordered, and wrong-revision text with export blocked. Rebuilding from the approved source restored a valid package. These corruptions changed candidate contents without changing the approved manuscript.

The complete pytest run passed **63 tests**, including five Streamlit AppTest workflows using the actual app's widgets and backend. Those interface tests covered the successful path, missing-line failure and recovery, wrong revision and fresh approval requirements, saved history, and unavailable live AI. Browser rendering and download clicks remain unverified because the environment blocked the local browser route. The [verification record](verification.md) separates executed checks from remaining gaps.

The live adapter is implemented with a structured response schema and mocked provider tests. No live API call has been verified. The project makes no claims about customers, measured productivity gains, revenue, or a fully autonomous publishing business.

## What I would take forward

The useful result is an inspectable approval boundary and a handoff that has to match its source. The next steps would be to run the live adapter with an authorized test account, observe an editor using the workflow, and improve span allocation around actual editorial needs. Shared use would require authenticated reviewers and a source of audit records outside a user's editable local database.

Those would be separate changes with their own evidence. A passing text-integrity check still says nothing about whether a story is good or a book is ready to print.
