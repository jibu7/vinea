import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SidebarNav, navIntents } from "./module-nav";

const allPermissions = new Set(
  navIntents.flatMap((intent) => intent.items.map((item) => item.permission).filter(Boolean)) as string[],
);

// The Maintenance and Transactions sections start expanded (module-nav.tsx); Enquiries
// and Reports start collapsed, so assertions below stick to the two open-by-default ones.

describe("SidebarNav permission filtering", () => {
  it("shows every live (non phase-tagged) item once its permission is granted", () => {
    render(<SidebarNav permissions={allPermissions} />);

    expect(screen.getByText("Company details")).toBeInTheDocument();
    expect(screen.getByText("Journal batches")).toBeInTheDocument();
    expect(screen.getByText("Cashbook batches")).toBeInTheDocument();
  });

  it("hides a live item once its permission is missing", () => {
    render(<SidebarNav permissions={new Set()} />);

    expect(screen.queryByText("Company details")).not.toBeInTheDocument();
    expect(screen.queryByText("Journal batches")).not.toBeInTheDocument();
  });

  it("always renders phase-tagged items, disabled, regardless of permissions", () => {
    render(<SidebarNav permissions={new Set()} />);

    const items = screen.getByText("Items"); // Inventory, still P5
    expect(items).toBeInTheDocument();
    expect(items.tagName).toBe("SPAN"); // disabled items render as inert text, not a link
    expect(screen.getAllByText("P5").length).toBeGreaterThan(0);
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
    expect(screen.getByText("Items")).toBeInTheDocument(); // phase-tagged, always visible
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
