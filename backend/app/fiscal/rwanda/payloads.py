"""Every EBM request and response, as pydantic models.

**Field names are RRA's, spelled exactly as the documents spell them** — `taxblAmtB`,
`vsdcRcptPbctDate`, `imptItemSttsCd` — and this module plus its siblings under `rwanda/` are
the only place in the product where that is true. Everything above the adapter speaks the DTOs
in `app.fiscal.mapping`, and `tests/fiscal/test_boundary.py` fails the build if that stops
being so.

Models rather than dicts, for one reason worth stating: a payload built as a dict is checked
by the revenue authority, once, in production. A model is checked here — a mistyped
`taxblAmtB` is an error at construction and a test, not a `881` on somebody's invoice.

**Money on the wire is `NUMBER 18,2`, sent as a JSON number.** Rwanda's base currency has *no*
decimal places, so nearly every amount crosses this boundary as a whole number of francs —
which is exactly what the documents' own samples show (`"taxAmt":30508`, `"taxAmt":534`). The
conversion happens in exactly one place (`Money` below), and a `Decimal` becomes a float only
there: everything upstream computes in `Decimal`, which is rule 6 with a revenue authority on
the other end of it.

Responses are modelled `extra="allow"`: RRA adds fields, and a response that carried something
new should not fail to parse. Requests are `extra="forbid"`, because a field this code invented
is a field RRA will ignore while the build believes it was sent.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

#: Two decimal places, half-up. Half-up rather than banker's rounding because it is what
#: `app.kernel.money` does and the two must not disagree about the same figure — and because
#: the RRA certification checkpoint sheet spells the same rule out in the same words
#: ("round values of tax on two decimals, <5 down, >=5 up", row 47).
WIRE_EXPONENT = Decimal("0.01")


def _to_wire_amount(value: Decimal | int | float | str) -> int | float:
    """A JSON **number**, integral where the value is.

    RRA's own samples are unquoted and mixed: `"taxblAmt":200000`, `"taxAmt":30508`,
    `"totTaxAmt":100677.97`. A quoted `"200000.00"` very probably parses on their side — the
    field is a Jackson `BigDecimal` — but "probably" is not a thing to find out in production,
    and matching the published samples costs nothing.

    `int` when the two-decimal value is whole, which on a base currency with no decimal places
    is every amount except an inclusive unit price. The `Decimal` is computed exactly and
    becomes a float only here, at the JSON boundary, because JSON has no decimal type: a
    two-decimal value below ~2^53 round-trips through a float exactly, and nothing downstream
    of this line does arithmetic.
    """
    quantized = Decimal(str(value)).quantize(WIRE_EXPONENT, rounding=ROUND_HALF_UP)
    integral = quantized.to_integral_value()
    return int(integral) if quantized == integral else float(quantized)


#: `NUMBER 18,2` — amounts and quantities on the wire.
Money = Annotated[
    Decimal, PlainSerializer(_to_wire_amount, return_type=int | float, when_used="json")
]


class _Request(BaseModel):
    """Every request body. `extra="forbid"` catches a field this code invented."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _Response(BaseModel):
    """Every response body. `extra="allow"` so a field RRA adds does not break parsing."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# --- The envelope every response arrives in -----------------------------------------------


class ResultEnvelope(_Response):
    """`resultCd` / `resultMsg` / `resultDt`, and whatever `data` the call returns.

    Every call comes back in this shape, including the refusals — which is why a refusal is an
    outcome in this design rather than an exception: the authority answered, it just said no,
    and the outbox has to record what it said.
    """

    resultCd: str
    resultMsg: str | None = None
    resultDt: str | None = None
    data: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.resultCd == "000"


class _DeviceScopedRequest(_Request):
    """`tin` + `bhfId` on everything. The taxpayer and the branch are the device's identity,
    and every call is made as one device."""

    tin: str
    bhfId: str
    #: Present on the `osdc` profile only — the adapter adds it, the models never default it.
    cmcKey: str | None = None


class _WatermarkedRequest(_DeviceScopedRequest):
    """The `lastReqDt` discipline: send the watermark you last succeeded with.

    `"20180520000000"` is the documents' own "everything" value. Sending it every time would
    work and would re-download the whole classification each sync, which is why the watermark
    is stored — and stored **only after a `000`**, so a failed sync cannot skip the rows
    published between the two calls.
    """

    lastReqDt: str


# --- Initialization -----------------------------------------------------------------------


class InitRequest(_DeviceScopedRequest):
    dvcSrlNo: str


class InitInfo(_Response):
    """What initialization returns.

    `cmcKey`, `intrlKey` and `sgnKey` are the three secrets this phase holds. They are parsed
    here, encrypted immediately by the device service, and never leave it again — not to a
    log, not to an endpoint, not into a stored payload.

    The `last*` counters are what makes this call double as *verification*: an outbox row
    whose answer never arrived is resolved by re-initializing and comparing, not by resending.
    """

    tin: str | None = None
    taxprNm: str | None = None
    bhfId: str | None = None
    bhfNm: str | None = None
    bhfOpenDt: str | None = None
    prvncNm: str | None = None
    dstrtNm: str | None = None
    sctrNm: str | None = None
    locDesc: str | None = None
    hqYn: str | None = None
    mgrNm: str | None = None
    mgrTelNo: str | None = None
    mgrEmail: str | None = None
    sdcId: str | None = None
    mrcNo: str | None = None
    dvcId: str | None = None
    cmcKey: str | None = None
    intrlKey: str | None = None
    sgnKey: str | None = None
    lastSaleInvcNo: int | None = None
    lastPchsInvcNo: int | None = None
    lastSaleRcptNo: int | None = None
    lastPchsRcptNo: int | None = None
    lastInvcNo: int | None = None


class InitData(_Response):
    info: InitInfo


# --- Codes and classifications ------------------------------------------------------------


class CodeListRequest(_WatermarkedRequest):
    pass


class CodeDetail(_Response):
    cd: str
    cdNm: str
    cdDesc: str | None = None
    useYn: str = "Y"
    srtOrd: int | None = None
    userDfnCd1: str | None = None
    userDfnCd2: str | None = None
    userDfnCd3: str | None = None


class CodeClass(_Response):
    cdCls: str
    cdClsNm: str | None = None
    cdClsDesc: str | None = None
    useYn: str = "Y"
    userDfnNm1: str | None = None
    userDfnNm2: str | None = None
    userDfnNm3: str | None = None
    dtlList: list[CodeDetail] = Field(default_factory=list)


class CodeListData(_Response):
    clsList: list[CodeClass] = Field(default_factory=list)


class ItemClassRequest(_WatermarkedRequest):
    pass


class ItemClass(_Response):
    itemClsCd: str
    itemClsNm: str
    itemClsLvl: int | None = None
    taxTyCd: str | None = None
    mjrTgYn: str | None = None
    useYn: str = "Y"


class ItemClassData(_Response):
    itemClsList: list[ItemClass] = Field(default_factory=list)


# --- Customers ----------------------------------------------------------------------------


class CustomerRequest(_DeviceScopedRequest):
    custmTin: str


class Customer(_Response):
    tin: str | None = None
    taxprNm: str | None = None
    taxprSttsCd: str | None = None
    prvncNm: str | None = None
    dstrtNm: str | None = None
    sctrNm: str | None = None
    locDesc: str | None = None


class CustomerData(_Response):
    custList: list[Customer] = Field(default_factory=list)


# --- Items --------------------------------------------------------------------------------


class SaveItemRequest(_DeviceScopedRequest):
    """Register or update one item. Idempotent on `itemCd`, which is why a changed item is
    re-registered under the same code rather than given a new one: a second code would orphan
    every receipt already issued against the first."""

    itemCd: str
    itemClsCd: str
    itemTyCd: str
    itemNm: str
    itemStdNm: str | None = None
    orgnNatCd: str
    pkgUnitCd: str
    qtyUnitCd: str
    taxTyCd: str
    btchNo: str | None = None
    bcd: str | None = None
    dftPrc: Money
    grpPrcL1: Money | None = None
    grpPrcL2: Money | None = None
    grpPrcL3: Money | None = None
    grpPrcL4: Money | None = None
    grpPrcL5: Money | None = None
    addInfo: str | None = None
    sftyQty: Money | None = None
    isrcAplcbYn: str = "N"
    useYn: str = "Y"
    regrId: str
    regrNm: str
    modrId: str
    modrNm: str

    @field_validator("itemNm")
    @classmethod
    def _truncate_name(cls, value: str) -> str:
        return value[:200]


# --- Sales --------------------------------------------------------------------------------


class SalesItem(_Request):
    """One receipt line.

    `dcAmt` is **the residue**, not a discount anybody keyed. `prc` is a VAT-inclusive unit
    price at two decimals; multiplied by the quantity it need not equal the line's posted
    gross, because the posted figure was rounded to the franc. The difference has to go
    somewhere the header buckets still add up, and the discount field is the only one the
    documents do not derive from another — so it takes it, and `dcRt` carries the discount an
    operator actually keyed. Whether RRA tolerates that is one of the two sandbox questions.
    """

    itemSeq: int
    itemCd: str | None = None
    itemClsCd: str
    itemNm: str
    bcd: str | None = None
    pkgUnitCd: str
    pkg: Money
    qtyUnitCd: str
    qty: Money
    prc: Money
    splyAmt: Money
    dcRt: Money
    dcAmt: Money
    isrccCd: str | None = None
    isrccNm: str | None = None
    isrcRt: Money | None = None
    isrcAmt: Money | None = None
    taxTyCd: str
    taxblAmt: Money
    taxAmt: Money
    totAmt: Money


class SalesReceipt(_Request):
    """The `receipt` block: what prints, and who it prints for."""

    custTin: str | None = None
    custMblNo: str | None = None
    rptNo: int
    trdeNm: str | None = None
    adrs: str | None = None
    topMsg: str | None = None
    btmMsg: str | None = None
    prchrAcptcYn: str = "N"


class SaveSalesRequest(_DeviceScopedRequest):
    """A sale or a refund. One model for both, because RRA's is one endpoint: `rcptTyCd`
    says which, and a refund adds `orgInvcNo` and `rfdRsnCd`."""

    invcNo: int
    orgInvcNo: int = 0
    custTin: str | None = None
    custNm: str | None = None
    salesTyCd: str = "N"
    rcptTyCd: str
    pmtTyCd: str
    salesSttsCd: str = "02"
    cfmDt: str
    salesDt: str
    stockRlsDt: str | None = None
    cnclReqDt: str | None = None
    cnclDt: str | None = None
    rfdDt: str | None = None
    rfdRsnCd: str | None = None
    totItemCnt: int
    taxblAmtA: Money
    taxblAmtB: Money
    taxblAmtC: Money
    taxblAmtD: Money
    taxRtA: Money
    taxRtB: Money
    taxRtC: Money
    taxRtD: Money
    taxAmtA: Money
    taxAmtB: Money
    taxAmtC: Money
    taxAmtD: Money
    totTaxblAmt: Money
    totTaxAmt: Money
    totAmt: Money
    prchrAcptcYn: str = "N"
    remark: str | None = None
    regrId: str
    regrNm: str
    modrId: str
    modrNm: str
    #: The purchase code a business customer supplies. RRA refuses a business sale without one
    #: (881), which is why it is a posting refusal in Vinea rather than a payload failure.
    prcOrdCd: str | None = None
    receipt: SalesReceipt
    itemList: list[SalesItem]


class SalesResponseData(_Response):
    """The signed receipt, in whichever of the two shapes the profile returns.

    Both are modelled here rather than in two classes, and `normalize_receipt()` in the
    adapter reads whichever arrived — the difference between the profiles is six field names,
    and two classes would push that difference one layer further up for no gain.
    """

    #: `vsdc` names the per-type counter `rcptNo`; `osdc` names it `curRcptNo`.
    rcptNo: int | None = None
    curRcptNo: int | None = None
    totRcptNo: int | None = None
    intrlData: str | None = None
    rcptSign: str | None = None
    #: `vsdc` stamps `vsdcRcptPbctDate`; `osdc` stamps `sdcDateTime`.
    vsdcRcptPbctDate: str | None = None
    sdcDateTime: str | None = None
    sdcId: str | None = None
    mrcNo: str | None = None
    totTaxblAmt: Money | None = None
    totTaxAmt: Money | None = None
    totAmt: Money | None = None


# --- Purchases ----------------------------------------------------------------------------


class PurchaseItem(_Request):
    itemSeq: int
    itemCd: str | None = None
    itemClsCd: str
    itemNm: str
    bcd: str | None = None
    spplrItemClsCd: str | None = None
    spplrItemCd: str | None = None
    spplrItemNm: str | None = None
    pkgUnitCd: str
    pkg: Money
    qtyUnitCd: str
    qty: Money
    prc: Money
    splyAmt: Money
    dcRt: Money
    dcAmt: Money
    taxTyCd: str
    taxblAmt: Money
    taxAmt: Money
    totAmt: Money
    itemExprDt: str | None = None


class SavePurchaseRequest(_DeviceScopedRequest):
    """A purchase this taxpayer made. `regTyCd` `M` registers one Vinea originated; `A`
    confirms one RRA is already holding, with RRA's own figures."""

    invcNo: int
    orgInvcNo: int = 0
    spplrTin: str | None = None
    spplrBhfId: str | None = None
    spplrNm: str | None = None
    spplrInvcNo: int | None = None
    regTyCd: str
    pchsTyCd: str
    rcptTyCd: str
    pmtTyCd: str
    pchsSttsCd: str
    cfmDt: str
    pchsDt: str
    wrhsDt: str | None = None
    cnclReqDt: str | None = None
    cnclDt: str | None = None
    rfdDt: str | None = None
    totItemCnt: int
    taxblAmtA: Money
    taxblAmtB: Money
    taxblAmtC: Money
    taxblAmtD: Money
    taxRtA: Money
    taxRtB: Money
    taxRtC: Money
    taxRtD: Money
    taxAmtA: Money
    taxAmtB: Money
    taxAmtC: Money
    taxAmtD: Money
    totTaxblAmt: Money
    totTaxAmt: Money
    totAmt: Money
    remark: str | None = None
    regrId: str
    regrNm: str
    modrId: str
    modrNm: str
    itemList: list[PurchaseItem]


class PurchaseFeedRequest(_WatermarkedRequest):
    pass


class PurchaseFeedItem(_Response):
    itemSeq: int
    itemCd: str | None = None
    itemClsCd: str | None = None
    itemNm: str | None = None
    pkgUnitCd: str | None = None
    pkg: Money | None = None
    qtyUnitCd: str | None = None
    qty: Money | None = None
    prc: Money | None = None
    splyAmt: Money | None = None
    dcRt: Money | None = None
    dcAmt: Money | None = None
    taxTyCd: str | None = None
    taxblAmt: Money | None = None
    taxAmt: Money | None = None
    totAmt: Money | None = None


class PurchaseFeedSale(_Response):
    spplrTin: str
    spplrBhfId: str | None = None
    spplrNm: str | None = None
    spplrInvcNo: int
    rcptTyCd: str | None = None
    pmtTyCd: str | None = None
    cfmDt: str | None = None
    salesDt: str | None = None
    stockRlsDt: str | None = None
    totItemCnt: int | None = None
    taxblAmtA: Money | None = None
    taxblAmtB: Money | None = None
    taxblAmtC: Money | None = None
    taxblAmtD: Money | None = None
    taxAmtA: Money | None = None
    taxAmtB: Money | None = None
    taxAmtC: Money | None = None
    taxAmtD: Money | None = None
    totTaxblAmt: Money | None = None
    totTaxAmt: Money | None = None
    totAmt: Money | None = None
    itemList: list[PurchaseFeedItem] = Field(default_factory=list)


class PurchaseFeedData(_Response):
    saleList: list[PurchaseFeedSale] = Field(default_factory=list)


# --- Stock --------------------------------------------------------------------------------


class StockItem(_Request):
    itemSeq: int
    itemCd: str | None = None
    itemClsCd: str
    itemNm: str
    bcd: str | None = None
    pkgUnitCd: str
    pkg: Money
    qtyUnitCd: str
    qty: Money
    itemExprDt: str | None = None
    prc: Money
    splyAmt: Money
    totDcAmt: Money
    taxblAmt: Money
    taxTyCd: str
    taxAmt: Money
    totAmt: Money


class SaveStockIoRequest(_DeviceScopedRequest):
    """One stock movement. `sarNo` is gapless per device from the `FSAR` run, and the movement
    follows the sale that caused it — VSDC §3.1 requires the invoice information first."""

    sarNo: int
    orgSarNo: int = 0
    regTyCd: str
    custTin: str | None = None
    custNm: str | None = None
    custBhfId: str | None = None
    sarTyCd: str
    ocrnDt: str
    totItemCnt: int
    totTaxblAmt: Money
    totTaxAmt: Money
    totAmt: Money
    remark: str | None = None
    regrId: str
    regrNm: str
    modrId: str
    modrNm: str
    itemList: list[StockItem]


class SaveStockMasterRequest(_DeviceScopedRequest):
    """On-hand for **one** item, snapshotted when the outbox row was *enqueued*.

    One item per call, not a list: §3.3.8.3's request object is flat and its sample is a single
    `itemCd`/`rsdQty` pair. That shape suits the outbox anyway — decision 10 queues one
    `stock_master` row per (item, branch) touched, so each row is one call and a row that fails
    does not take the others with it.

    Snapshotted at enqueue rather than at send, because by the time the queue drains the shelf
    has moved on, and what RRA is being told is what was true at the moment of the movement it
    has just received.
    """

    itemCd: str
    rsdQty: Money
    regrId: str
    regrNm: str
    modrId: str
    modrNm: str


class StockMoveRequest(_WatermarkedRequest):
    pass


# --- Imports ------------------------------------------------------------------------------


class ImportItemsRequest(_WatermarkedRequest):
    pass


class ImportItem(_Response):
    taskCd: str
    dclDe: str | None = None
    itemSeq: int
    dclNo: str | None = None
    hsCd: str | None = None
    itemNm: str | None = None
    imptItemSttsCd: str | None = None
    orgnNatCd: str | None = None
    exptNatCd: str | None = None
    pkg: Money | None = None
    pkgUnitCd: str | None = None
    qty: Money | None = None
    qtyUnitCd: str | None = None
    totWt: Money | None = None
    netWt: Money | None = None
    spplrNm: str | None = None
    agntNm: str | None = None
    invcFcurAmt: Money | None = None
    invcFcurCd: str | None = None
    invcFcurExcrt: Money | None = None


class ImportItemsData(_Response):
    itemList: list[ImportItem] = Field(default_factory=list)


class UpdateImportItemRequest(_DeviceScopedRequest):
    """Approve or reject one customs line, naming the Vinea item it became.

    Approval is a **compliance acknowledgment**: it moves no stock and posts nothing. The goods
    reached the ledger through a goods receipt, and saying so twice would double them.
    """

    taskCd: str
    dclDe: str
    itemSeq: int
    hsCd: str | None = None
    itemClsCd: str | None = None
    itemCd: str | None = None
    imptItemSttsCd: str
    remark: str | None = None
    modrNm: str
    modrId: str
