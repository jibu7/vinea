/**
 * Order-entry wire types (P6).
 *
 * Only what the **Order defaults** screen needs so far. Orders, receipts and landed costs
 * arrive with their screens at steps 7 and 8; a type written ahead of the screen that reads
 * it is a guess about a shape nobody has rendered yet.
 *
 * Enum-valued fields import from `@/lib/api-enums` and are re-exported here, so a screen
 * holding an `OrderDefaults` never has to reach past this module for the policy union.
 */
import type { BackorderPolicy } from "@/lib/api-enums";

export type { BackorderPolicy };

export interface OrderDefaults {
  grn_accrual_account_id: number | null;
  purchase_price_variance_account_id: number | null;
  landed_cost_clearing_account_id: number | null;
  backorder_policy: BackorderPolicy;
  /** Read-only on this screen. Sales and purchase orders default their warehouse from it,
   * but it is the *inventory* default and the Inventory defaults screen owns writing it —
   * `update_order_defaults` refuses it with `unknown_gl_setting`. */
  default_warehouse_id: number | null;
}

/**
 * A PUT body. Every key is optional and only those present are written, so the screen sends
 * what it holds; clearing an account is refused server-side with `required_setting` rather
 * than accepted and failed at the next GRN.
 */
export interface OrderDefaultsPayload {
  grn_accrual_account_id?: number;
  purchase_price_variance_account_id?: number;
  landed_cost_clearing_account_id?: number;
  backorder_policy?: BackorderPolicy;
}
