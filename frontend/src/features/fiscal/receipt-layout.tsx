"use client";

import { useTranslations } from "next-intl";
import { QRCodeSVG } from "qrcode.react";
import { formatMoney, formatQuantity } from "@/lib/format";
import type { PartnerDocumentDetail } from "@/features/subledger/types";
import type { ReceiptBlock } from "./types";

/**
 * The CIS receipt, per the 2018 *Technical Specification of CIS for VSDC* §13 (a normal sale)
 * and §14 (a refund).
 *
 * **Print-only, and the whole page.** A fiscal receipt is not a decorated document detail: it
 * is a prescribed layout an inspector reads, and what the screen shows around it — allocations,
 * the entry links, the Reverse button — has no place on it. So the detail screen hides its own
 * panels at print time when this exists, and this is `hidden print:block`.
 *
 * Three rules from the specification are load-bearing here rather than cosmetic:
 *
 * 1. **Every programmed rate above zero prints on every receipt**, and a zero rate prints only
 *    when it was used (§7.22–7.23). That decision is the server's — `classes[].used` — so a
 *    company whose programmed rates change does not need this file edited.
 * 2. **Internal Data and Receipt Signature are dashed every four characters** (§7.24). They are
 *    read aloud and typed in by hand; the grouping is what makes that possible.
 * 3. **A refund prints negative amounts** and `REF. NORMAL RECEIPT#` naming the original's
 *    total counter (§14) — the same receipt this document's own `NS` produced.
 *
 * The QR carries §7.24.7's content, which the backend assembles (`qr_payload`): nothing here
 * composes it, because the receipt signature is part of it and a client that built its own
 * would eventually build a different one.
 *
 * The **RRA logo** (§7.29) is a bordered placeholder until the owner supplies the asset — named
 * as a plan deviation in the phase report rather than approximated.
 */
export function CisReceiptLayout({
  block,
  document,
  currency,
}: {
  block: ReceiptBlock;
  document: PartnerDocumentDetail;
  currency: { code: string; decimalPlaces: number; symbol: string | null };
}) {
  const t = useTranslations("fiscal.receipt");
  const isRefund = block.refund_of_tot_rcpt_no !== null;
  /** A refund is declared to RRA with positive amounts under `rcptTyCd R` and **printed**
   * negative (§14). One sign convention on the wire, another on the paper. */
  const sign = isRefund ? -1 : 1;
  const money = (value: string | number) =>
    formatMoney(sign * Number(value), currency, { showCode: false });

  return (
    <div
      data-testid="cis-receipt"
      className="hidden font-mono text-[11px] leading-tight text-black print:block"
    >
      {block.is_copy && (
        <p
          data-testid="receipt-copy-watermark"
          className="mb-1 text-center text-lg font-bold tracking-[0.4em]"
        >
          {t("copy")}
        </p>
      )}

      {/* --- The taxpayer, §4 a–d ------------------------------------------------------- */}
      <div className="border-b border-black pb-2 text-center">
        <div className="mx-auto mb-1 flex h-10 w-24 items-center justify-center border border-black text-[9px]">
          {t("logoPlaceholder")}
        </div>
        <p className="text-sm font-bold">{block.taxpayer_name}</p>
        <p>{t("tin", { value: block.taxpayer_tin ?? t("unknown") })}</p>
        <p>{block.branch_name}</p>
        {block.branch_address && <p>{block.branch_address}</p>}
      </div>

      {/* --- What this receipt is, §5 and §14 ------------------------------------------- */}
      <div className="border-b border-black py-2 text-center">
        <p className="font-bold">{isRefund ? t("refundHeading") : t("saleHeading")}</p>
        {isRefund && (
          <p data-testid="receipt-refund-of">
            {t("refundOf", { value: block.refund_of_tot_rcpt_no ?? 0 })}
          </p>
        )}
        <p>{t("clientId", { value: block.customer_tin ?? t("none") })}</p>
        <p>{block.customer_name}</p>
        {block.purchase_code && <p>{t("purchaseCode", { value: block.purchase_code })}</p>}
      </div>

      {/* --- The lines, §4 e–h ---------------------------------------------------------- */}
      <table className="w-full border-b border-black py-2">
        <tbody>
          {document.lines.map((line) => (
            <tr key={line.id} className="align-top">
              <td className="py-0.5">
                <p>{line.description ?? ""}</p>
                <p className="pl-2">
                  {t("unitTimesQuantity", {
                    price: formatMoney(Number(line.unit_price), currency, { showCode: false }),
                    quantity: formatQuantity(Number(line.quantity), 2),
                  })}
                </p>
                {Number(line.discount_percent) !== 0 && (
                  <p className="pl-2">
                    {t("lineDiscount", { percent: formatQuantity(Number(line.discount_percent), 2) })}
                  </p>
                )}
              </td>
              <td className="py-0.5 text-right tabular-nums">{money(line.gross_amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* --- The totals, §4 i–k and §7.22–7.23 ------------------------------------------ */}
      <dl className="border-b border-black py-2">
        <TotalRow label={t("total")} value={money(block.gross_total)} bold testId="receipt-total" />
        {block.classes
          .filter((line) => Number(line.rate) > 0 || line.used)
          .map((line) => (
            <TotalRow
              key={line.tax_class}
              label={t("totalClass", { code: line.tax_class, rate: line.rate })}
              value={money(line.taxable)}
            />
          ))}
        {block.classes
          .filter((line) => Number(line.rate) > 0)
          .map((line) => (
            <TotalRow
              key={`tax-${line.tax_class}`}
              label={t("totalTaxClass", { code: line.tax_class })}
              value={money(line.tax)}
            />
          ))}
        <TotalRow
          label={t("totalTax")}
          value={money(block.tax_total)}
          bold
          testId="receipt-total-tax"
        />
        {Number(block.discount_total) !== 0 && (
          <TotalRow label={t("totalDiscount")} value={money(block.discount_total)} />
        )}
      </dl>

      {block.is_copy && (
        <p data-testid="receipt-copy-warning" className="py-2 text-center font-bold">
          {t("notOfficial")}
        </p>
      )}

      {/* --- How it was paid, and how many things, §4 l–m ------------------------------- */}
      <div className="border-b border-black py-2">
        <p>
          {t("paymentMethod", {
            value: block.payment_method ? t(`paymentMethodLabel.${block.payment_method}`) : t("none"),
          })}
        </p>
        <p data-testid="receipt-items-number">
          {t("itemsNumber", { value: block.items_count })}
        </p>
      </div>

      {/* --- The SDC block, §7.24 ------------------------------------------------------- */}
      <div className="border-b border-black py-2">
        <p className="text-center font-bold">{t("sdcInformation")}</p>
        <p>{t("sdcDateTime", { date: sdcDate(block.sdc_datetime), time: sdcTime(block.sdc_datetime) })}</p>
        <p>{t("sdcId", { value: block.sdc_id })}</p>
        <p data-testid="receipt-counter">
          {t("receiptNumber", { value: block.receipt_number })}
        </p>
        <p className="break-all">{t("internalData", { value: dashed(block.intrl_data) })}</p>
        <p className="break-all">{t("receiptSignature", { value: dashed(block.rcpt_sign) })}</p>
        <div className="flex justify-center py-2">
          <QRCodeSVG value={block.qr_payload} size={96} level="M" />
        </div>
      </div>

      {/* --- The CIS's own footer, §4 n -------------------------------------------------- */}
      <div className="py-2">
        <p data-testid="receipt-document-number">
          {t("cisReceiptNumber", { value: block.document_number })}
        </p>
        <p>{t("cisDate", { value: block.document_date })}</p>
        <p>{t("mrc", { value: block.mrc_no ?? t("unknown") })}</p>
      </div>
    </div>
  );
}

function TotalRow({
  label,
  value,
  bold,
  testId,
}: {
  label: string;
  value: string;
  bold?: boolean;
  testId?: string;
}) {
  return (
    <div className={`flex justify-between ${bold ? "font-bold" : ""}`}>
      <dt>{label}</dt>
      <dd className="tabular-nums" data-testid={testId}>
        {value}
      </dd>
    </div>
  );
}

/**
 * Four characters, a dash, four characters — §7.24's own grouping.
 *
 * Not decoration: an inspector reads these aloud off the paper and a clerk types them into
 * MyRRA, and a twenty-six-character run with no grouping is where that goes wrong. Applied to
 * the value as it came, never to a value this file reformatted first.
 */
export function dashed(value: string): string {
  return (value.match(/.{1,4}/g) ?? []).join("-");
}

/** `dd/mm/yyyy` from the SDC's own timestamp. Deliberately not `formatDate`: this is the
 * authority's format on a prescribed layout, not the user's locale. */
function sdcDate(iso: string): string {
  const [date] = iso.split("T");
  const [year, month, day] = date.split("-");
  return `${day}/${month}/${year}`;
}

function sdcTime(iso: string): string {
  const time = iso.split("T")[1] ?? "";
  return time.slice(0, 8);
}
