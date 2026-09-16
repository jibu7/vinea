# Payload samples

One JSON document per pydantic model in `app/fiscal/rwanda/payloads.py`, round-tripped by
`tests/fiscal/test_payloads.py`.

**Provenance, stated plainly.** The phase asks for "the document's own JSON sample". The three
RRA PDFs could not be fetched in the build environment — `docs/rra/README.md` says why — so
these are **reconstructed** from the field lists and formats transcribed in
`docs/rra/contract-notes.md`, not copied out of a document. They are therefore a check that the
models parse and re-emit the shape the code was written to, and *not* evidence that the shape
matches what RRA publishes.

What turns them into that evidence, in order:

1. Pin the PDFs (`docs/rra/README.md` says how) and replace each file here with the document's
   own example, keeping the file name. The round-trip test does not change.
2. Step 5's live run against `sdcsandbox.rra.gov.rw`, which answers the question empirically.

Until then this gap is named as a plan deviation in the step-1 report rather than papered over.
