"""The decision-6 payload map, and the property that keeps it honest.

Three claims, and all three are load-bearing:

1. **Σ items == every header bucket, and Σ buckets == the totals.** A payload that disagrees
   with itself is the single most likely defect in a mapping, and RRA refuses it (`802`/`803`).
2. **`taxAmt == round(taxblAmt × r / (100 + r))` per line, at the base currency's decimals.**
   It holds by construction — `split_tax` computes inclusive tax the same way — and the
   sandbox checks it within one base unit, so a line taxed exclusively and reported inclusively
   is caught in CI rather than in Kigali.
3. **Two rounding residues, censused rather than asserted away.** `prc` is an inclusive unit
   price at two decimals, so on a zero-decimal base it rarely multiplies back to the posted
   gross and the difference goes to `dcAmt`. And the posted *tax* is rounded to the base
   currency's decimals while the wire is `NUMBER 18,2` — on RWF the two agree only when the
   gross is a multiple of 59. Neither has a known right answer; both are questions step 5's
   live run settles, and a number nobody printed is a question nobody answers.

   The second one was found by running this property at 300 examples, not by reading the
   specification: at two examples every drawn amount happened to be clean.

The census is printed, not enforced. The P6 lesson was the opposite — that a printed census is
a courtesy until somebody puts a floor under it — and the difference is what each one is *for*:
P6's counted refusals a guard had to provoke, which is a coverage claim. This one counts a
rounding residue whose right value is unknown, which is an observation. A floor on an unknown
would be a number somebody made up.
"""

import itertools
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.fiscal.mapping import FiscalLine, FiscalParty, FiscalRefund, FiscalSale
from app.fiscal.rwanda import builders, codes
from app.models.fiscalization import (
    FiscalDevice,
    FiscalProfile,
    FiscalTaxType,
    PaymentMethod,
)

ZERO = Decimal(0)
HUNDRED = Decimal(100)
PENNY = Decimal("0.01")

#: The two rounding censuses of the module docstring. Printed at the end of the module — see
#: there for why neither is a floor.
_RESIDUE_CENSUS: dict[str, int] = {}


def _count(key: str) -> None:
    _RESIDUE_CENSUS[key] = _RESIDUE_CENSUS.get(key, 0) + 1


def emitted_amount(value: int | float) -> Decimal:
    """A wire amount, back as an exact `Decimal`.

    Through `str`, never `Decimal(float)`: the wire carries JSON numbers, and `Decimal(1.18)`
    is the binary expansion `1.17999…`, which would make this property fail on arithmetic it
    introduced itself. The same reason `app.kernel.money` never builds a `Decimal` from a
    float — stated here because a test that gets this wrong looks like a product bug.
    """
    return Decimal(str(value))


@pytest.fixture(scope="module", autouse=True)
def _report_residue():  # noqa: ANN202
    yield
    if _RESIDUE_CENSUS:
        print("\n[fiscal] rounding census:", dict(sorted(_RESIDUE_CENSUS.items())))


def device(profile: FiscalProfile = FiscalProfile.VSDC) -> FiscalDevice:
    return FiscalDevice(
        id=1,
        company_id=1,
        branch_id=1,
        profile=profile,
        base_url="http://ebm.sandbox",
        tin="999000099",
        bhf_id="00",
        dvc_srl_no="SRL",
    )


def line(
    *,
    sequence: int = 1,
    quantity: Decimal,
    inclusive_price: Decimal,
    taxable: Decimal,
    tax: Decimal,
    tax_class: FiscalTaxType = FiscalTaxType.B,
    discount: Decimal = ZERO,
) -> FiscalLine:
    return FiscalLine(
        sequence=sequence,
        item_code="RW2NTXU0000001",
        item_class_code="5059020800",
        name="Rugari Red 75cl",
        quantity=quantity,
        unit_price_inclusive=inclusive_price,
        taxable_amount=taxable,
        tax_amount=tax,
        tax_class=tax_class,
        # The programmed rate that goes with the class, so the factory cannot build a line
        # whose rate and class disagree — the wire tax is derived from this.
        tax_rate_pct=Decimal(codes.PROGRAMMED_RATES[tax_class.value]),
        package_unit="NT",
        quantity_unit="U",
        discount_percent=discount,
    )


def sale(lines: tuple[FiscalLine, ...], **overrides) -> FiscalSale:  # noqa: ANN003
    defaults = {
        "invoice_no": 1,
        "document_number": "INV-000001",
        "document_date": date(2026, 3, 16),
        "posted_at": datetime(2026, 3, 16, 10, 30, tzinfo=UTC),
        "party": FiscalParty(name="Customer C Ltd", tin="100000001", phone="0788000001"),
        "lines": lines,
        "payment_method": PaymentMethod.CREDIT,
        "purchase_code": "AB12CD",
        "actor_id": "17",
        "actor_name": "Owner",
        "branch_name": "Head Office",
    }
    return FiscalSale(**(defaults | overrides))


# --- The worked example ---------------------------------------------------------------------


def test_the_tape_row_one_payload_is_built_by_hand_and_matched() -> None:
    """Row 1 of the acceptance tape, with every figure worked by hand in this comment.

    10 x X at 2 000 exclusive, standard-rated: inclusive unit price 2 000 x 1.18 = 2 360.00,
    supply 23 600.00, posted gross 23 600 and posted tax 3 600 (= 23 600 x 18/118). One
    zero-rated service line at 10 000. So bucket B is 23 600 taxable / 3 600 tax, bucket C is
    10 000 / 0, and the totals are 33 600 / 3 600.
    """
    request = builders.build_sale_request(
        device(),
        sale(
            (
                line(
                    quantity=Decimal(10),
                    inclusive_price=Decimal("2360.00"),
                    taxable=Decimal(23600),
                    tax=Decimal(3600),
                ),
                line(
                    sequence=2,
                    quantity=Decimal(1),
                    inclusive_price=Decimal("10000.00"),
                    taxable=Decimal(10000),
                    tax=ZERO,
                    tax_class=FiscalTaxType.C,
                ),
            )
        ),
    )
    emitted = request.model_dump(mode="json", exclude_none=True)

    assert emitted["taxblAmtB"] == 23600
    assert emitted["taxAmtB"] == 3600
    assert emitted["taxblAmtC"] == 10000
    assert emitted["totTaxblAmt"] == 33600
    assert emitted["totTaxAmt"] == 3600
    assert emitted["totAmt"] == 33600
    assert emitted["itemList"][0]["prc"] == 2360
    assert emitted["itemList"][0]["splyAmt"] == 23600
    assert emitted["itemList"][0]["dcAmt"] == 0
    assert emitted["rcptTyCd"] == codes.SalesReceiptType.SALE
    assert emitted["pmtTyCd"] == codes.PaymentType.CREDIT
    assert emitted["prcOrdCd"] == "AB12CD"
    assert emitted["salesTyCd"] == "N"


def test_the_programmed_rates_are_on_every_payload_even_where_no_line_uses_them() -> None:
    """CIS §7.22–7.23: every rate above zero prints on every receipt. A payload that carried
    `taxRtB` only when a B line existed would produce a receipt missing the row."""
    emitted = builders.build_sale_request(
        device(),
        sale(
            (
                line(
                    quantity=Decimal(5),
                    inclusive_price=Decimal("1000.00"),
                    taxable=Decimal(5000),
                    tax=ZERO,
                    tax_class=FiscalTaxType.A,
                ),
            )
        ),
    ).model_dump(mode="json")

    assert (emitted["taxRtA"], emitted["taxRtB"]) == (0, 18)
    assert emitted["taxblAmtB"] == 0


def test_a_discount_keeps_its_rate_and_lands_in_the_amount() -> None:
    """Tape row 2: 3 x X at 2 000 less 10%. Inclusive price 2 360.00, supply 7 080.00, posted
    gross 6 372, so `dcAmt` is 708.00 — the money value of the discount — and `dcRt` is the
    percentage somebody keyed."""
    emitted = builders.build_sale_request(
        device(),
        sale(
            (
                line(
                    quantity=Decimal(3),
                    inclusive_price=Decimal("2360.00"),
                    taxable=Decimal(6372),
                    tax=Decimal(972),
                    discount=Decimal(10),
                ),
            ),
            party=FiscalParty(name="Walk-in"),
            payment_method=PaymentMethod.CASH,
            purchase_code=None,
        ),
    ).model_dump(mode="json", exclude_none=True)

    item = emitted["itemList"][0]
    assert (item["splyAmt"], item["dcRt"], item["dcAmt"]) == (7080, 10, 708)
    assert item["taxblAmt"] == 6372
    assert emitted["pmtTyCd"] == codes.PaymentType.CASH
    assert "custTin" not in emitted, "a walk-in sale carries no TIN"


def test_a_refund_names_its_original_and_its_reason() -> None:
    """Positive amounts under `rcptTyCd R` — the receipt type says the direction. Whether RRA
    expects negatives instead is decision 6's second sandbox question, and it changes this
    builder and nothing else."""
    refund = FiscalRefund(
        invoice_no=3,
        document_number="CRN-000001",
        document_date=date(2026, 3, 17),
        posted_at=datetime(2026, 3, 17, 9, 0, tzinfo=UTC),
        party=FiscalParty(name="Customer C Ltd", tin="100000001"),
        lines=(
            line(
                quantity=Decimal(2),
                inclusive_price=Decimal("2360.00"),
                taxable=Decimal(4720),
                tax=Decimal(720),
            ),
        ),
        payment_method=PaymentMethod.CREDIT,
        purchase_code="AB12CD",
        original_invoice_no=1,
        reason_code=codes.RefundReason.REFUND,
    )

    emitted = builders.build_refund_request(device(), refund).model_dump(mode="json")

    assert emitted["rcptTyCd"] == "R"
    assert emitted["orgInvcNo"] == 1
    assert emitted["rfdRsnCd"] == "06"
    assert emitted["totTaxblAmt"] == 4720


def test_the_stock_release_date_is_absent_on_a_services_only_invoice() -> None:
    """A release date on an invoice that moves nothing would have RRA expecting a stock
    movement that never comes."""
    services = builders.build_sale_request(
        device(),
        sale(
            (line(quantity=Decimal(1), inclusive_price=Decimal("11800.00"),
                  taxable=Decimal(11800), tax=Decimal(1800)),),
            releases_stock=False,
        ),
    ).model_dump(mode="json", exclude_none=True)
    goods = builders.build_sale_request(
        device(),
        sale(
            (line(quantity=Decimal(1), inclusive_price=Decimal("11800.00"),
                  taxable=Decimal(11800), tax=Decimal(1800)),),
            releases_stock=True,
        ),
    ).model_dump(mode="json", exclude_none=True)

    assert "stockRlsDt" not in services
    assert goods["stockRlsDt"] == goods["cfmDt"]


def test_timestamps_are_kigali_local_rather_than_utc() -> None:
    """A UTC `cfmDt` would put a 22:30 sale on the following day's Z report — Kigali is
    UTC+2, so the two differ for the last two hours of every trading day."""
    late = sale(
        (line(quantity=Decimal(1), inclusive_price=Decimal("1180.00"),
              taxable=Decimal(1180), tax=Decimal(180)),),
        posted_at=datetime(2026, 3, 16, 22, 30, tzinfo=UTC),
    )

    emitted = builders.build_sale_request(device(), late).model_dump(mode="json")

    assert emitted["cfmDt"] == "20260317003000"


def test_an_unmappable_payment_method_would_fail_loudly() -> None:
    """The map is total on purpose. A `.get` with a default would send every future payment
    method as "other" and nobody would find out."""
    assert set(builders.PAYMENT_TYPE_BY_METHOD) == set(PaymentMethod)


# --- The property ---------------------------------------------------------------------------

#: A 0-decimal base (RWF) and a 2-decimal one, because the residue only exists on the first and
#: a property that ran on one would prove half of it.
BASE_DECIMALS = st.sampled_from([0, 2])


@st.composite
def random_lines(draw, base_decimals: int) -> tuple[FiscalLine, ...]:  # noqa: ANN001
    """Lines whose posted figures are built the way the ledger builds them.

    Exclusive unit price and quantity are drawn; the gross is rounded to the base currency's
    decimals, and the tax is then computed *inclusively* from that gross — which is exactly
    what `split_tax` does, so the property is asserting the adapter agrees with the kernel
    rather than with itself.
    """
    exponent = Decimal(1).scaleb(-base_decimals)
    count = draw(st.integers(min_value=1, max_value=4))
    lines: list[FiscalLine] = []
    for sequence in range(1, count + 1):
        tax_class = draw(st.sampled_from(list(FiscalTaxType)))
        rate = Decimal(codes.PROGRAMMED_RATES[tax_class.value])
        quantity = Decimal(draw(st.integers(min_value=1, max_value=50)))
        net_price = Decimal(draw(st.integers(min_value=1, max_value=500_000)))
        gross = (net_price * quantity * (HUNDRED + rate) / HUNDRED).quantize(exponent)
        tax = (gross * rate / (HUNDRED + rate)).quantize(exponent)
        lines.append(
            line(
                sequence=sequence,
                quantity=quantity,
                inclusive_price=builders.inclusive_unit_price(net_price, rate),
                taxable=gross,
                tax=tax,
                tax_class=tax_class,
            )
        )
    return tuple(lines)


@pytest.mark.slow
@given(base_decimals=BASE_DECIMALS, data=st.data())
def test_the_header_is_the_sum_of_the_lines_and_the_tax_is_the_inclusive_rate(
    base_decimals: int, data: st.DataObject
) -> None:
    lines = data.draw(random_lines(base_decimals))

    emitted = builders.build_sale_request(device(), sale(lines)).model_dump(mode="json")

    # 1. Every bucket is the sum of its own lines.
    for tax_class in FiscalTaxType:
        # The **wire's** taxable amount, not the posted one: `taxblAmt` is `splyAmt - dcAmt`
        # derived from the inclusive price, and a header summing the postings would be a header
        # disagreeing with its own lines — which is the defect this clause exists to catch.
        expected_taxable = sum(
            (builders.line_taxable(item) for item in lines if item.tax_class == tax_class),
            ZERO,
        )
        # The **derived** tax, not the posted one: since the build follows Sage's convention
        # of sending `taxblAmt x r/(100+r)`, a header summing the posted tax would be a header
        # disagreeing with its own lines. That is the defect this clause exists to catch, so it
        # has to be computed the way the lines are.
        expected_tax = sum(
            (builders.line_tax(item) for item in lines if item.tax_class == tax_class),
            ZERO,
        )
        assert emitted_amount(emitted[f"taxblAmt{tax_class.value}"]) == expected_taxable
        assert emitted_amount(emitted[f"taxAmt{tax_class.value}"]) == expected_tax

    # 2. The totals are the sum of the buckets — never a separate computation.
    assert emitted_amount(emitted["totTaxblAmt"]) == sum(
        (emitted_amount(emitted[f"taxblAmt{c.value}"]) for c in FiscalTaxType), ZERO
    )
    assert emitted_amount(emitted["totTaxAmt"]) == sum(
        (emitted_amount(emitted[f"taxAmt{c.value}"]) for c in FiscalTaxType), ZERO
    )
    assert emitted_amount(emitted["totAmt"]) == emitted_amount(emitted["totTaxblAmt"])
    assert emitted["totItemCnt"] == len(lines)

    # 3. RRA's own relations, all the way down from `prc`. These are what the VSDC engine
    # validates, and the build derives every one of them rather than reading the posting.
    for item, source in zip(emitted["itemList"], lines, strict=True):
        rate = Decimal(codes.PROGRAMMED_RATES[source.tax_class.value])
        price = emitted_amount(item["prc"])
        supply = emitted_amount(item["splyAmt"])
        discount = emitted_amount(item["dcAmt"])
        taxable = emitted_amount(item["taxblAmt"])
        quantity = emitted_amount(item["qty"])

        assert supply == (price * quantity).quantize(PENNY), "splyAmt is prc x qty"
        assert discount == (supply * emitted_amount(item["dcRt"]) / HUNDRED).quantize(PENNY), (
            "dcAmt is splyAmt x dcRt / 100"
        )
        assert taxable == supply - discount, "taxblAmt is splyAmt - dcAmt"
        assert emitted_amount(item["totAmt"]) == taxable, "totAmt is taxblAmt"
        assert emitted_amount(item["taxAmt"]) == (taxable * rate / (HUNDRED + rate)).quantize(
            PENNY
        ), "taxAmt is taxblAmt x r/(100+r)"
        if emitted_amount(item["dcRt"]) == ZERO:
            assert discount == ZERO, (
                "a line nobody discounted may not carry a discount amount: that is the phantom "
                "discount the VSDC engine rejects"
            )

        # 4. The census, rewritten to measure the thing that now actually varies.
        #
        # It used to count how often the residue landed in `dcAmt`. There is no such residue
        # any more — the wire derives from `prc` and the ledger stays as posted — so what is
        # worth counting is the **gap between them**: how far the wire's taxable amount and tax
        # sit from the figures the posting holds, per line. That is the number step 4's VAT
        # return has to reconcile, and the number step 5's live run should be read against.
        taxable_gap = taxable - builders.wire(source.taxable_amount)
        tax_gap = emitted_amount(item["taxAmt"]) - builders.wire(source.tax_amount)
        _count(
            f"{base_decimals}dp: taxblAmt equals the ledger"
            if taxable_gap == ZERO
            else f"{base_decimals}dp: taxblAmt differs from the ledger"
        )
        _count(
            f"{base_decimals}dp: taxAmt equals the ledger"
            if tax_gap == ZERO
            else f"{base_decimals}dp: taxAmt differs from the ledger"
        )
        assert abs(taxable_gap) <= quantity, (
            "the wire's taxable amount may differ from the ledger's by at most the rounding of "
            f"the unit price, once per unit: {taxable} against {source.taxable_amount}"
        )


#: Hypothesis reuses a function-scoped fixture across the examples of one test, so the sandbox
#: ledger below accumulates exactly as a real device's would — and a real device refuses a
#: repeated `invcNo` with `994`. The invoice number therefore comes from a counter rather than
#: a draw: drawing one would make the property fail on a collision that is the *sandbox being
#: right*, which is the opposite of what it is there to catch.
_INVOICE_NUMBERS = itertools.count(1)


@pytest.mark.slow
@given(base_decimals=BASE_DECIMALS, data=st.data())
def test_a_payload_the_sandbox_accepts_is_what_the_builder_produces(
    base_decimals: int, data: st.DataObject, sandbox_client  # noqa: ANN001
) -> None:
    """The property that matters most: every payload this builder produces is one RRA's own
    checks pass. The sandbox implements those checks, so this is the end-to-end form of the
    three assertions above."""
    lines = data.draw(random_lines(base_decimals))
    request = builders.build_sale_request(
        device(), sale(lines, invoice_no=next(_INVOICE_NUMBERS))
    )

    response = sandbox_client.post(
        "/trnsSales/saveSales", json=request.model_dump(mode="json", exclude_none=True)
    ).json()

    assert response["resultCd"] == codes.RESULT_OK, response["resultMsg"]


def test_a_refund_carries_no_negative_number() -> None:
    """Sage's standard, held over the **whole payload** rather than the fields somebody thought
    of: a refund is positive everywhere, and its direction is `rcptTyCd R` plus `orgInvcNo`.

    Walked recursively because that is the only version that survives a new field. The VSDC
    schema rejects a negative quantity outright, so a builder that "helpfully" negated a refund
    would fail at the authority rather than here — which is a much worse place to find out.
    """
    refund = builders.build_refund_request(
        device(),
        FiscalRefund(
            **{
                **{
                    field: value
                    for field, value in vars(
                        sale((line(quantity=Decimal(2), inclusive_price=Decimal("1180"),
                                   taxable=Decimal(2360), tax=Decimal(360)),))
                    ).items()
                },
                "original_invoice_no": 7,
                "reason_code": codes.RefundReason.REFUND,
            }
        ),
    ).model_dump(mode="json")

    negatives: list[str] = []

    def walk(node, path: str) -> None:  # noqa: ANN001
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, (int, float)) and not isinstance(node, bool) and node < 0:
            negatives.append(f"{path}={node}")
        elif isinstance(node, str) and node.startswith("-"):
            negatives.append(f"{path}={node}")

    walk(refund, "")

    assert negatives == [], f"a refund payload must be positive throughout: {negatives}"
    assert refund["rcptTyCd"] == "R", "the direction is the receipt type"
    assert refund["orgInvcNo"] == 7, "and the original it reverses"


#: Four live EBM 2.1 receipts, as (label, line gross amounts, the printed `Total Tax B`).
#:
#: These are the acceptance data for the rounding rule, and they are *literals worked from
#: paper* rather than anything this code produced. The receipts themselves are deliberately not
#: in the repository (`docs/rra/README.md` says why); they are cited by invoice number so the
#: figures can be checked against the originals.
#:
#: **TESKO 10057 is the one that matters.** On the other three the two candidate methods agree
#: by luck. On that one they differ by a centime, and the printed figure is the per-line sum:
#: that single receipt is the whole evidence for rounding each line before adding, rather than
#: splitting the invoice total.
LIVE_RECEIPT_TAX = (
    ("TESKO 10057", (15000, 27500, 5000, 24000, 2800, 22500, 16000, 6000), "18122.04"),
    ("M TOOLS 7329", (2000, 3500, 9500, 29000), "6711.86"),
    ("HUSSEIN 9434", (70560, 47040, 164640), "43053.56"),
    ("MTN NSIN000032912", (95000,), "14491.53"),
)


@pytest.mark.parametrize(
    ("label", "grosses", "printed_tax"),
    LIVE_RECEIPT_TAX,
    ids=[case[0] for case in LIVE_RECEIPT_TAX],
)
def test_the_header_tax_matches_what_a_live_device_printed(
    label: str, grosses: tuple[int, ...], printed_tax: str
) -> None:
    lines = tuple(
        line(
            sequence=index,
            quantity=Decimal(1),
            inclusive_price=Decimal(gross),
            taxable=Decimal(gross),
            tax=ZERO,  # unused: the wire tax is derived, which is the point of this test
        )
        for index, gross in enumerate(grosses, start=1)
    )

    emitted = builders.build_sale_request(device(), sale(lines)).model_dump(mode="json")

    assert emitted_amount(emitted["taxAmtB"]) == Decimal(printed_tax), label
    assert emitted_amount(emitted["totTaxAmt"]) == Decimal(printed_tax), label


def test_splitting_the_invoice_total_would_disagree_with_the_receipt_that_proves_it() -> None:
    """Anti-vacuity, and the reason the parametrised case above is not just four green ticks.

    Three of those four receipts pass under either rounding method. If somebody "simplifies"
    `bucket_totals` to split the invoice total once, three of them stay green and only TESKO
    10057 goes red — so this states outright that the two methods differ there, and by how
    much. Without it, a reader has no way to tell which of the four is doing the work.
    """
    grosses = LIVE_RECEIPT_TAX[0][1]
    rate = Decimal(codes.PROGRAMMED_RATES[FiscalTaxType.B.value])

    per_line = sum(
        ((Decimal(g) * rate / (HUNDRED + rate)).quantize(PENNY) for g in grosses), ZERO
    )
    whole_total = (Decimal(sum(grosses)) * rate / (HUNDRED + rate)).quantize(PENNY)

    assert per_line == Decimal("18122.04"), "what the device printed"
    assert whole_total == Decimal("18122.03"), "what splitting the total once would send"
    assert per_line - whole_total == Decimal("0.01")
