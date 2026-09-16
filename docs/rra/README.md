# RRA documents this phase is written to

Phase 7 implements Rwanda fiscalization against three published RRA documents. They are
**pinned** here — version, date, source URL and SHA-256 — so that a field name in
`backend/app/fiscal/rwanda/payloads.py` can be traced to a document somebody can open, and so
that a later revision of one of them is a visible change rather than a silent one.

| File | Document | Version | Date | SHA-256 |
|---|---|---|---|---|
| `vsdc-api-v1.0.5.pdf` | VSDC API documentation | 1.0.5 | 2023-05-16 | **not pinned — see below** |
| `osdc-v1.0.1.pdf` | OSDC documentation | 1.0.1 | 2022-04-08 | **not pinned — see below** |
| `cis-for-vsdc-v1.0.pdf` | Technical Specification of CIS for VSDC | 1.0 | 2018-03 | **not pinned — see below** |

Sources:

* VSDC — `https://www.rra.gov.rw/fileadmin/user_upload/VSDC_SPECIFICATION_DOCUMENT_v1.0.5_okay.pdf`
* OSDC — `https://www.rra.gov.rw/fileadmin/user_upload/osdc_documentation_v1.0.1_2022-04-08.pdf`
* CIS — `https://www.rra.gov.rw/fileadmin/user_upload/CIS_for_VSDC_technical_Specifications_New.pdf`

## The PDFs are not in the tree, and that is a deviation

The build environment this step ran in reaches the internet through a policy-enforcing egress
proxy, and `www.rra.gov.rw` is **not on its allow-list**: every request to it is answered
`403` at the CONNECT, before TLS. The three files therefore could not be fetched, and this
file records what is known about them rather than a hash of something that was never
downloaded. Routing around the denial was not attempted.

**What the code was written against instead** is `docs/rra/contract-notes.md`: the paths, code
tables, field names and formats as the phase prompt reproduces them, transcribed with the
document section each comes from. Every RRA name in `app/fiscal/rwanda/` cites a section there.
That is a weaker provenance than the documents themselves, and it is named as a plan deviation
in the step-1 report for exactly that reason.

## Pinning them

Drop the three PDFs in beside this file under the names in the table, then:

```bash
cd docs/rra && sha256sum vsdc-api-v1.0.5.pdf osdc-v1.0.1.pdf cis-for-vsdc-v1.0.pdf
```

and replace the three **not pinned** cells with the hashes. From that moment
`backend/tests/fiscal/test_rra_documents.py` enforces them: a file present whose hash is not
the one recorded here fails the suite, which is the property worth having — a revised document
silently replacing the one the payloads were built against is precisely the change nobody
would otherwise notice.

Until the cells are filled in, that test asserts only what it can: that this table names all
three documents, that every file present is named in it, and that a recorded hash matches.

## Owner questions still open at step 1

1. **Has RRA published a newer revision of any of the three?** Precondition (b) of the phase
   prompt asks for this before anything is built against them. Unanswered here, and it cannot
   be answered from inside this environment — the source host is unreachable.
2. **Is there an RRA certification checkpoint sheet, a sample EBM 2.1 receipt from the test
   environment, or the RRA logo asset?** The checkpoint sheet governs the receipt layout and
   the X/Z content where it differs from the 2018 document, and a sample receipt overrides the
   QR format. Until they arrive the 2018 document is the contract and a bordered placeholder
   stands where the logo goes (step 8).
3. **Does the owner hold RRA test-environment access** — a TIN, branch id and device serial
   approved on `myrratest.rra.gov.rw`? Step 5's live run needs it; steps 1–4 do not.
