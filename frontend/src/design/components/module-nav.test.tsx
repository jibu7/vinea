import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SidebarNav } from "./module-nav";
import { navIntents } from "@/design/nav-tree";

// Flattened, because a row's `permission` may be a list meaning "any of" — the valuation
// report's is. Taking only the first would grant a set that is not "every permission the
// tree names", and the "shows every live item" test below would stop being true of one row
// for a reason that has nothing to do with what it is checking.
const allPermissions = new Set(
  navIntents.flatMap((intent) =>
    intent.items.flatMap((item) =>
      item.permission === undefined ? [] : [item.permission].flat(),
    ),
  ),
);

// The Maintenance and Transactions sections start expanded (module-nav.tsx); Enquiries
// and Reports start collapsed, so assertions below stick to the two open-by-default ones.

describe("SidebarNav permission filtering", () => {
  it("shows every live (non phase-tagged) item once its permission is granted", () => {
    render(<SidebarNav permissions={allPermissions} />);

    expect(screen.getByText("Company details")).toBeInTheDocument();
    // Two "Journal batches" rows since P5 step 7 — the GL one and the Inventory one — so
    // this reads both rather than one, the way the P4 block below handles a repeated label.
    expect(screen.getAllByText("Journal batches")).toHaveLength(2);
    expect(screen.getByText("Cashbook batches")).toBeInTheDocument();
  });

  it("hides a live item once its permission is missing", () => {
    render(<SidebarNav permissions={new Set()} />);

    expect(screen.queryByText("Company details")).not.toBeInTheDocument();
    expect(screen.queryAllByText("Journal batches")).toHaveLength(0);
  });

  it("always renders phase-tagged items, disabled, regardless of permissions", () => {
    render(<SidebarNav permissions={new Set()} />);

    // Order Entry's GRV, still P6 — its screen is step 7. This assertion has now moved
    // twice for the same reason: it read "Items" until P5 step 6 and "Order defaults" until
    // P6 step 6, each time because the row it named went live. It has to point at something
    // actually still tagged or it stops testing the tagging at all.
    const tagged = screen.getByText("GRV");
    expect(tagged).toBeInTheDocument();
    expect(tagged.tagName).toBe("SPAN"); // disabled items render as inert text, not a link
    expect(screen.getAllByText("P6").length).toBeGreaterThan(0);
  });

  it("renders a live item's label as a link, not disabled text", () => {
    render(<SidebarNav permissions={allPermissions} />);

    const companyDetails = screen.getByText("Company details");
    expect(companyDetails.closest("a")).not.toBeNull();
  });

  it("filters within a section: granted items show, ungranted ones don't, phase-tagged ones always do", () => {
    render(<SidebarNav permissions={new Set(["gl:setup_manage"])} />);

    expect(screen.getByText("Chart of accounts")).toBeInTheDocument();
    expect(screen.getByText("Transaction types")).toBeInTheDocument();
    expect(screen.queryByText("Company details")).not.toBeInTheDocument();
    expect(screen.queryByText("Foreign currency")).not.toBeInTheDocument();
    expect(screen.queryByText("Customers")).not.toBeInTheDocument(); // live since P4, AR-gated
    expect(screen.queryByText("Items")).not.toBeInTheDocument(); // live since P5, INV-gated
    // Live since P6 step 6 and gated on the order-entry permissions, so a GL-only role no
    // longer sees it — the phase-tagged example in this section is now the P12 BOM row.
    expect(screen.queryByText("Order defaults")).not.toBeInTheDocument();
    expect(screen.getByText("BOM items & defaults")).toBeInTheDocument(); // phase-tagged, always visible
  });

  it("keeps navIntents' declared order and grouping stable for the palette to reuse", () => {
    const maintenance = navIntents.find((i) => i.label === "Maintenance");
    expect(maintenance?.items[0]).toEqual(
      expect.objectContaining({ label: "Company details", permission: "company:read" }),
    );
  });
});

describe("P4 maintenance screens", () => {
  const arApPermissions = new Set(["ar:reports_view", "ar:setup_manage", "ap:reports_view", "ap:setup_manage"]);

  it.each([
    ["Customers", "/maintenance/customers"],
    ["Sales reps", "/maintenance/sales-reps"],
    ["Payment terms", "/maintenance/payment-terms"],
    ["Ageing bucket sets", "/maintenance/ageing-bucket-sets"],
    ["Suppliers", "/maintenance/suppliers"],
    ["Rename customer code", "/maintenance/rename-partner-code?role=ar"],
    ["Rename supplier code", "/maintenance/rename-partner-code?role=ap"],
  ])("links %s to %s with no phase tag left", (label, href) => {
    render(<SidebarNav permissions={arApPermissions} />);

    const link = screen.getAllByText(label)[0].closest("a");
    expect(link).not.toBeNull();
    expect(link).toHaveAttribute("href", href);
  });

  it("points AR and AP transaction types at their own module screens", () => {
    const maintenance = navIntents.find((i) => i.label === "Maintenance")!;
    const byModule = (module: string) =>
      maintenance.items.find((item) => item.module === module && item.label === "Transaction types");
    expect(byModule("Accounts Receivable")?.href).toBe("/maintenance/ar-transaction-types");
    expect(byModule("Accounts Payable")?.href).toBe("/maintenance/ap-transaction-types");
  });
});

describe("P5 transaction screens", () => {
  // Counts is deliberately not on this set: P5 step 9 split `inv:count_enter` out, so the
  // adjustment permission alone no longer reaches the sheet. The pair of tests below the
  // table pins both halves of that.
  const invPermissions = new Set(["inv:transactions_adjust", "inv:count_enter"]);

  it.each([
    ["Inventory", "Journal batches", "/inventory/journal-batches/new"],
    ["Inventory", "Transfers", "/inventory/transfers"],
    ["Inventory", "Adjustments", "/inventory/adjustments/new"],
    ["Inventory", "Counts", "/inventory/counts"],
  ])("links %s → %s to %s with no phase tag left", (module, label, href) => {
    const transactions = navIntents.find((i) => i.label === "Transactions")!;
    const item = transactions.items.find((row) => row.module === module && row.label === label);
    expect(item?.href).toBe(href);
    expect(item?.phase).toBeUndefined();

    render(<SidebarNav permissions={invPermissions} />);
    const links = screen.getAllByText(label).map((node) => node.closest("a")).filter(Boolean);
    expect(links.map((a) => a?.getAttribute("href"))).toContain(href);
  });

  it("leaves no P5 tag in the Transactions block once step 7 has landed", () => {
    const transactions = navIntents.find((i) => i.label === "Transactions")!;
    expect(transactions.items.filter((item) => item.phase === "P5")).toEqual([]);
  });

  // A stock-taker must not receive adjustment rights as a side effect of counting, and the
  // nav is half of that promise: gating Counts on the adjustment permission would hand every
  // adjuster the sheet and leave a counting-only role with nothing to click.
  it("opens Counts to a stock-taker holding only inv:count_enter", () => {
    render(<SidebarNav permissions={new Set(["inv:count_enter"])} />);

    expect(screen.getByText("Counts").closest("a")).toHaveAttribute("href", "/inventory/counts");
    expect(screen.queryByText("Adjustments")).not.toBeInTheDocument();
    expect(screen.queryByText("Transfers")).not.toBeInTheDocument();
    expect(screen.queryAllByText("Journal batches")).toHaveLength(0);
  });

  // And `inv:count_process` alone opens it too, matching `_require_count`: a controller who
  // posts counts is not locked out of the sheet they post.
  it("opens Counts to a holder of inv:count_process alone, and nothing else in the block", () => {
    render(<SidebarNav permissions={new Set(["inv:count_process"])} />);

    expect(screen.getByText("Counts").closest("a")).toHaveAttribute("href", "/inventory/counts");
    expect(screen.queryByText("Adjustments")).not.toBeInTheDocument();
  });

  it("does not open Counts to adjustment rights alone", () => {
    render(<SidebarNav permissions={new Set(["inv:transactions_adjust"])} />);

    expect(screen.getByText("Adjustments")).toBeInTheDocument();
    expect(screen.queryByText("Counts")).not.toBeInTheDocument();
  });
});

describe("P5 maintenance screens", () => {
  const invPermissions = new Set(["inv:reports_view", "inv:setup_manage", "inv:item_rename"]);

  it.each([
    ["Items", "/maintenance/inventory-items"],
    ["Warehouses", "/maintenance/warehouses"],
    ["Barcodes", "/maintenance/barcodes"],
    ["Units of measure", "/maintenance/uom-categories"],
    ["Rename item code", "/maintenance/rename-item-code"],
  ])("links %s to %s with no phase tag left", (label, href) => {
    render(<SidebarNav permissions={invPermissions} />);

    const link = screen.getAllByText(label)[0].closest("a");
    expect(link).not.toBeNull();
    expect(link).toHaveAttribute("href", href);
  });

  it("gives Inventory its own transaction types and defaults screens", () => {
    // "Transaction types" and "Defaults" are labels four modules share, so these are matched
    // on the module rather than the label — the same shape as the AR/AP assertion above.
    const maintenance = navIntents.find((i) => i.label === "Maintenance")!;
    const inventory = (label: string) =>
      maintenance.items.find((item) => item.module === "Inventory" && item.label === label);
    expect(inventory("Transaction types")?.href).toBe("/maintenance/inv-transaction-types");
    expect(inventory("Defaults")?.href).toBe("/maintenance/inventory-defaults");
  });

  it("leaves no P5 tag anywhere in the tree once every P5 screen has landed", () => {
    // Transactions, Enquiries and Reports still carry P5 rows — steps 7 and 8 clear those.
    // What this pins is the Maintenance block: a tag left behind on a screen that exists is
    // a nav item nobody can reach, which is how the row would go unnoticed.
    const maintenance = navIntents.find((i) => i.label === "Maintenance")!;
    const tagged = maintenance.items.filter((item) => item.phase === "P5");
    expect(tagged).toEqual([]);
  });
});

describe("P5 enquiry and report screens", () => {
  const invPermissions = new Set(["inv:reports_view"]);

  it.each([
    ["Enquiries", "Item enquiry", "/inventory/enquiry"],
    ["Reports", "Movement", "/inventory/reports/movement"],
    ["Reports", "Count", "/inventory/reports/counts"],
    ["Reports", "Transaction", "/inventory/reports/transactions"],
    ["Reports", "Valuation", "/inventory/reports/valuation"],
  ])("links %s → %s to %s with no phase tag left", (intentLabel, label, href) => {
    const intent = navIntents.find((i) => i.label === intentLabel)!;
    const item = intent.items.find((row) => row.module === "Inventory" && row.label === label);
    expect(item?.href).toBe(href);
    expect(item?.phase).toBeUndefined();

    render(<SidebarNav permissions={invPermissions} />);
    // Enquiries and Reports start collapsed, so the link is in the DOM only once its
    // section is open — open it by its heading, the way a person would.
    fireEvent.click(screen.getByRole("button", { name: intentLabel }));
    const links = screen.getAllByText(label).map((node) => node.closest("a")).filter(Boolean);
    expect(links.map((a) => a?.getAttribute("href"))).toContain(href);
  });

  // The one row gated on either permission. An accountant holding
  // `reporting:inventory_valuation_view` and no `inv:*` right can call the endpoint, so the
  // nav has to reach them too — and must still not hand them the other three reports.
  it("shows Valuation to a holder of reporting:inventory_valuation_view alone", () => {
    render(<SidebarNav permissions={new Set(["reporting:inventory_valuation_view"])} />);
    fireEvent.click(screen.getByRole("button", { name: "Reports" }));

    expect(screen.getByText("Valuation")).toBeInTheDocument();
    expect(screen.queryByText("Movement")).not.toBeInTheDocument();
    expect(screen.queryByText("Count")).not.toBeInTheDocument();
    expect(screen.queryByText("Transaction")).not.toBeInTheDocument();
  });

  it("leaves no P5 tag anywhere in the tree once step 8 has landed", () => {
    const tagged = navIntents.flatMap((intent) =>
      intent.items
        .filter((item) => item.phase === "P5")
        .map((item) => `${intent.label}/${item.module}/${item.label}`),
    );
    expect(tagged).toEqual([]);
  });
});

describe("P6 maintenance screens", () => {
  // Any one of the five order-entry permissions opens the screen, because `GET /oe/defaults`
  // accepts any one of them: a buyer who may raise purchase orders can read which account
  // their receipts will accrue into without also holding the right to change it.
  it.each([
    "oe:setup_manage",
    "oe:sales_orders_manage",
    "oe:purchase_orders_manage",
    "oe:grv_process",
    "oe:reports_view",
  ])("opens Order defaults to a holder of %s alone", (permission) => {
    render(<SidebarNav permissions={new Set([permission])} />);

    const link = screen.getByText("Order defaults").closest("a");
    expect(link).toHaveAttribute("href", "/maintenance/order-defaults");
  });

  it("leaves Order defaults untagged, and the other P6 rows tagged until step 7", () => {
    const maintenance = navIntents.find((i) => i.label === "Maintenance")!;
    const defaults = maintenance.items.find((item) => item.label === "Order defaults");
    expect(defaults?.phase).toBeUndefined();
    expect(defaults?.href).toBe("/maintenance/order-defaults");

    const transactions = navIntents.find((i) => i.label === "Transactions")!;
    expect(
      transactions.items.filter((item) => item.phase === "P6").map((item) => item.label),
    ).toEqual(["GRV", "Purchase order", "Sales order"]);
  });

  it("hides Order defaults from a role holding none of the order-entry permissions", () => {
    render(<SidebarNav permissions={new Set(["gl:setup_manage", "inv:reports_view"])} />);

    expect(screen.queryByText("Order defaults")).not.toBeInTheDocument();
  });
});
