import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SidebarNav } from "./module-nav";
import { navIntents } from "@/design/nav-tree";

const allPermissions = new Set(
  navIntents.flatMap((intent) => intent.items.map((item) => item.permission).filter(Boolean)) as string[],
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

    // Order Entry, still P6. This used to read "Items", which stopped being an example of a
    // phase-tagged row at P5 step 6 when its screen landed — the assertion has to point at
    // something actually still tagged or it stops testing the tagging at all.
    const tagged = screen.getByText("Order defaults");
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
    expect(screen.getByText("Order defaults")).toBeInTheDocument(); // phase-tagged, always visible
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
  const invPermissions = new Set(["inv:transactions_adjust"]);

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
