/**
 * Fiscalization wire types (P7).
 *
 * Step 6's half: the device, the two synced code tables and the TIN lookup — what Maintenance
 * needs before anything can be fiscalized at all. The queue, the receipts and the returns are
 * steps 7 and 8, and their shapes land with the screens that render them.
 *
 * Enum-valued fields import from `@/lib/api-enums`, which is generated from the Python enums
 * and never typed by hand (`backend/app/scripts/export_api_enums.py`, with
 * `backend/tests/test_api_enums_export.py` as the drift gate). A profile, an environment or a
 * device status spelled out in a screen file is the P5 step 6 defect exactly, and
 * `src/lib/api-enums.test.ts` now fails on one.
 */
import type {
  FiscalDeviceStatus,
  FiscalEnvironment,
  FiscalItemTypeCode,
  FiscalProfile,
  FiscalSyncKind,
  FiscalTaxType,
} from "@/lib/api-enums";

export type {
  FiscalDeviceStatus,
  FiscalEnvironment,
  FiscalItemTypeCode,
  FiscalProfile,
  FiscalSyncKind,
  FiscalTaxType,
};

/**
 * One EBM device, one branch.
 *
 * **No key, ever.** `cmc_key`, `intrl_key` and `sign_key` are the only secrets this phase
 * holds and no response model has a field for one — `has_keys` answers the question a screen
 * actually asks ("is this device holding its keys") without going near the values. The
 * backend pins that over the model rather than over one endpoint's output
 * (`tests/fiscal/test_key_redaction.py`), and this interface is the frontend saying the same
 * thing: there is nowhere here to put a key, so there is nothing to render by accident.
 */
export interface FiscalDevice {
  id: number;
  branch_id: number;
  profile: FiscalProfile;
  environment: FiscalEnvironment;
  base_url: string;
  /** Copied from the company at initialization; a later company rename refuses with
   * `tin_mismatch` rather than sending sales under a number the device never registered. */
  tin: string | null;
  bhf_id: string;
  dvc_srl_no: string;
  mrc_no: string | null;
  sdc_id: string | null;
  dvc_id: string | null;
  status: FiscalDeviceStatus;
  /** Per sync kind, the last thing the authority said it had published. */
  watermarks: Record<string, string>;
  last_success_at: string | null;
  last_error: string | null;
  has_keys: boolean;
}

export interface DeviceCreatePayload {
  branch_id: number;
  profile: FiscalProfile;
  environment: FiscalEnvironment;
  base_url: string;
  dvc_srl_no: string;
  bhf_id: string;
}

export interface DeviceSyncResult {
  device_id: number;
  kind: FiscalSyncKind;
  rows: number;
  watermark: string | null;
}

/** A row of one of the authority's own code tables (§4.6 and its neighbours). Synced, never
 * typed: `code_class` 04 is the tax classes A–D, 10 is the quantity units. */
export interface FiscalCode {
  code_class: string;
  code_class_name: string | null;
  code: string;
  name: string;
  is_active: boolean;
}

/** The authority's item classification — tens of thousands of rows, so the picker is a
 * typeahead over the server rather than a list the browser holds. */
export interface FiscalItemClass {
  item_cls_cd: string;
  item_cls_nm: string;
  item_cls_lvl: number | null;
  tax_ty_cd: string | null;
}

/** What the authority says about a TIN, at a moment. `found: false` is an answer, not a
 * failure — the authority's own "no such taxpayer" (884) arrives this way. */
export interface TinLookup {
  tin: string;
  found: boolean;
  name: string | null;
  status: string | null;
}

/** An item and what RRA holds about it. The Items screen reads the registration half of this
 * and shows it beside the four fields that produce it. */
export interface ItemRegistration {
  item_id: number;
  item_code: string;
  item_name: string;
  item_type: string;
  registered: boolean;
  item_cd: string | null;
  item_cls_cd: string | null;
  item_ty_cd: string | null;
  orgn_nat_cd: string | null;
  pkg_unit_cd: string | null;
  qty_unit_cd: string | null;
  tax_ty_cd: string | null;
  default_price_inclusive: string | null;
  barcode: string | null;
  active: boolean;
  registered_at: string | null;
  pending_rows: number;
  last_error: string | null;
}

/**
 * The code classes these screens read, named once rather than spelled at each call site.
 *
 * They are the authority's own numbering (VSDC §4) and the backend names the same four in
 * `app/fiscal/rwanda/codes.py`; a screen asking for "the packaging units" should not carry a
 * bare `"17"` any more than it should carry a bare `"vsdc"`.
 */
export const CODE_CLASS = {
  /** §4.1 — the tax classes A–D, which a Vinea tax code maps onto. */
  TAX_TYPE: "04",
  /** §4.4 — nations, the item's country of origin. */
  NATION: "05",
  /** §4.6 — quantity units, what a Vinea unit of measure maps onto. */
  QUANTITY_UNIT: "10",
  /** §4.5 — packaging units. */
  PACKAGING_UNIT: "17",
} as const;
