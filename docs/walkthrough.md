# Two-minute walkthrough

Start the app in a fresh workspace so the first button is **Load sample manuscript**. Keep the default recorded mode. This is an authored example with no API call. Allow a few extra seconds for each rerun or download when presenting live.

| Time | Action | Say |
| --- | --- | --- |
| 0:00–0:15 | Open **Manuscript** and click **Load sample manuscript**. | “This is Manuscript to Handoff. It follows a manuscript from editorial suggestions to an approved production package. The problem is simple: the text that leaves the workflow needs to be the text someone approved.” |
| 0:15–0:40 | Open **Edit & review**, click **Generate suggestions**, accept suggestion 1, reject suggestion 2, then **Save review decisions**. | “This demo loads an authored fixture. No model is being called. There is also an optional live adapter. I can accept this change and reject the added clock detail. Saving creates a new draft. It doesn't approve it.” |
| 0:40–0:55 | Open **Approval**, check the approval statement, and click **Approve current revision**. | “Approval is a separate action against this exact revision and hash. The original is still in history, along with both decisions.” |
| 0:55–1:10 | Open **Handoff**, click **Build handoff**, then **Run validation**. Point to the enabled **Download final handoff**. | “The candidate includes exact text, source spans, a brief, approval, and a validation report. Ordinary code compares its contents with the approved source. Export also reopens and checks the actual ZIP.” |
| 1:10–1:30 | Click **Remove a manuscript line**, then **Run validation**. Point to failed coverage and reconstruction checks and the disabled final download. | “Now I've removed real text from the candidate. The approved source hasn't changed. The validator finds the gap and blocks export. I can still download the failure report.” |
| 1:30–1:40 | Click **Rebuild from approved revision**, then **Run validation**. | “Rebuilding restores the source text. A fresh validation passes and the download becomes available again.” |
| 1:40–1:55 | Click **Use earlier revision**, then **Run validation**. Rebuild and validate again. | “Using the original revision also fails. It doesn't match the current approval. The same recovery gives us a clean candidate again.” |
| 1:55–2:05 | Point to **Evidence & history**, then return to the enabled download. | “The decisions and failed attempts survive a restart. This proves controlled text transfer. Pagination, design, and print readiness still need their own review.” |

If the workspace already has history, use **Load sample manuscript** to append a new sample draft; it does not reset earlier work. For the wrong-revision demonstration, choose that newly loaded sample's ID in **Earlier revision for demo**.

To reproduce the artifact evidence outside the UI:

```bash
python scripts/run_demo.py --output artifacts/demo
```

Check the [README](../README.md) for the actual verification performed. The live provider connection remains unverified without credentials.
