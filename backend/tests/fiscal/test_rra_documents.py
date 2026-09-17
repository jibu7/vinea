"""The pinned RRA documents.

`docs/rra/` holds what this phase is written to, and this is the gate that keeps it honest: a
revised specification silently replacing the one the payload field names were built against is
exactly the change nobody would otherwise notice. Every hash in the register is enforced, and
every file in the directory has to be in the register.

Deliberately **not** skipped when something is missing. A skipped test reads as "nothing to
see"; these say what is and is not known.
"""

import hashlib
import os
import re
from pathlib import Path

REPO_ROOT = Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[3])
RRA_DIR = REPO_ROOT / "docs" / "rra"
README = RRA_DIR / "README.md"
NOTES = RRA_DIR / "contract-notes.md"

#: file name → what the document is. The three specifications the adapter implements.
SPECIFICATIONS = {
    "VSDC_SPECIFICATION_DOCUMENT_v1.0.5_okay.pdf": "VSDC API documentation",
    "osdc_documentation_v1.0.1_2022-04-08.pdf": "OSDC documentation",
    "CIS_for_VSDC_technical_Specifications_New.pdf": (
        "Technical Specification of CIS for VSDC"
    ),
}

#: The owner-supplied material beside them: the certification checkpoint sheet and the logo CIS
#: §7.29 requires on every receipt.
#:
#: Five live EBM receipts were here while the adapter was written against them and have been
#: **withdrawn** — real taxpayers' TINs, trading names and telephone numbers do not belong in a
#: public repository once the evidence is recorded. What they proved is in `README.md` and
#: `contract-notes.md` §7 and §8, and is pinned by tests rather than by the files;
#: `test_no_live_receipt_is_committed` below keeps them from coming back.
SUPPORTING = {
    "EXCEL_SHEET_application_form_RRA_VSDC_okay(Compliance table).csv",
    "Rwanda-Revenue-Authority-logo.png",
}

#: A receipt is a PDF or an image that is not one of the files above. Named by shape rather
#: than by the five filenames, because the next one somebody drops in will have a different
#: name — a screenshot from a phone, a scan, a second vendor's sample.
RECEIPT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}

#: Everything the register has to account for, and the prose files that are the register.
PROSE = {"README.md", "contract-notes.md"}

SHA256 = re.compile(r"`?\b([0-9a-f]{64})\b`?")


def _readme() -> str:
    assert README.exists(), (
        "docs/rra/README.md is the register of the documents this phase is written to. "
        "Without it there is no record of which revision the payloads were built against."
    )
    return README.read_text(encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_specification_is_present() -> None:
    missing = sorted(name for name in SPECIFICATIONS if not (RRA_DIR / name).exists())

    assert missing == [], (
        f"these specifications are not pinned under docs/rra/: {missing}. The adapter's field "
        "names have no citable source without them."
    )


def test_the_register_names_every_document_with_its_version_and_source() -> None:
    text = _readme()

    for file_name, title in SPECIFICATIONS.items():
        assert file_name in text, f"{file_name} is not in the register"
        assert title in text, f"{title} is not named in the register"
    assert "rra.gov.rw" in text, "each entry carries the URL it came from"


def test_every_file_in_the_directory_is_accounted_for() -> None:
    """A document nobody recorded is one the code may have been written against silently."""
    known = set(SPECIFICATIONS) | SUPPORTING | PROSE
    unregistered = sorted(
        path.name for path in RRA_DIR.iterdir() if path.is_file() and path.name not in known
    )

    assert unregistered == [], (
        f"these files are in docs/rra/ but not accounted for here: {unregistered}. Add them to "
        "SUPPORTING with their hash in the register, or remove them."
    )


def test_every_specification_matches_the_hash_the_register_records() -> None:
    """The check that matters: a revised document is a visible change, not a silent one."""
    recorded = set(SHA256.findall(_readme()))
    assert recorded, "the register records no hashes at all"

    for file_name in SPECIFICATIONS:
        path = RRA_DIR / file_name
        digest = _digest(path)
        assert digest in recorded, (
            f"{file_name} hashes to {digest}, which the register does not record. If this is a "
            "newer revision, the payload field names have to be re-checked against it before "
            "the hash is updated — that is the whole point of pinning them."
        )


def test_every_supporting_file_present_is_hashed_too() -> None:
    """The checkpoint sheet governs the receipt layout and the X/Z content, and the live
    receipts are what step 8's print layout is built from. A replaced one changes the build's
    evidence, so it is pinned like a specification."""
    recorded = set(SHA256.findall(_readme()))
    unhashed = sorted(
        name
        for name in SUPPORTING
        if (RRA_DIR / name).exists() and _digest(RRA_DIR / name) not in recorded
    )

    assert unhashed == [], f"these supporting files are not hashed in the register: {unhashed}"


def test_the_contract_notes_cite_both_profiles_and_the_formats_the_code_implements() -> None:
    """`contract-notes.md` is the reading map over the specifications: it is what a reviewer
    checks `payloads.py` against without opening a PDF. It has to name the things the code
    actually implements."""
    assert NOTES.exists()
    text = NOTES.read_text(encoding="utf-8")

    for path in ("/trnsSales/saveSales", "/saveTrnsSalesOsdc", "/initializer/selectInitInfo"):
        assert path in text, f"{path} is not recorded in the contract notes"
    assert "§4.17" in text, "the item-code format is what `build_item_code` implements"
    assert "RW2NTBA0000012" in text, "the document's own item-code example"
    assert "#internal_data#receipt_signature" in text, "the QR content of CIS §7.24.7"
    assert "922" in text, "the ordering rule the outbox's FIFO exists for"


def test_the_checkpoint_sheet_still_says_what_the_build_relies_on() -> None:
    """Three rows the build is designed around. If RRA reissues the sheet and one of them
    changes, this fails here rather than at certification."""
    sheet = RRA_DIR / "EXCEL_SHEET_application_form_RRA_VSDC_okay(Compliance table).csv"
    text = sheet.read_text(encoding="utf-8", errors="replace")

    assert "round values of tax on two decimals" in text
    assert "The Refund receipt has always to be printed with a negative" in text
    assert "must not be able to issue receipt of any type if not connected" in text


def test_no_live_receipt_is_committed() -> None:
    """The withdrawal, kept withdrawn.

    Live receipts are how several of this phase's questions were settled, so the temptation to
    drop another one in here is real and reasonable. It is still a real taxpayer's TIN, trading
    name, address and telephone number in a public repository, and the evidence survives the
    file: every finding they produced is written into `README.md` and `contract-notes.md` and
    pinned by a test over `build_item_code` or the payload models.

    So: read the receipt, record what it shows, cite it by invoice number, and do not commit
    it. A specification or the RRA logo is a different thing and is listed above.
    """
    allowed = set(SPECIFICATIONS) | SUPPORTING
    stray = sorted(
        path.name
        for path in RRA_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in RECEIPT_SUFFIXES and path.name not in allowed
    )

    assert stray == [], (
        "these look like live receipts or unregistered binaries in a public repository: "
        f"{stray}. Record what the receipt shows in contract-notes.md and cite it by invoice "
        "number; do not commit the document itself."
    )
