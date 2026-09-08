import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { LineGrid, emptyLineGridRow, type LineGridRow } from "./line-grid";

const accountOptions = [
  { value: "1", label: "Cash" },
  { value: "2", label: "Sales" },
];

function Harness({ initialRows }: { initialRows: LineGridRow[] }) {
  const [rows, setRows] = useState(initialRows);
  return (
    <LineGrid mode="journal" rows={rows} onRowsChange={setRows} accountOptions={accountOptions} />
  );
}

function descriptionInputs(): HTMLInputElement[] {
  return screen.getAllByPlaceholderText("Line description");
}

describe("LineGrid keyboard model", () => {
  it("adds a row when Enter is pressed on the last row", () => {
    render(<Harness initialRows={[emptyLineGridRow()]} />);
    expect(descriptionInputs()).toHaveLength(1);

    fireEvent.keyDown(descriptionInputs()[0], { key: "Enter" });

    expect(descriptionInputs()).toHaveLength(2);
  });

  it("moves Enter to the next row instead of adding one when not on the last row", () => {
    render(<Harness initialRows={[emptyLineGridRow(), emptyLineGridRow()]} />);

    fireEvent.keyDown(descriptionInputs()[0], { key: "Enter" });

    expect(descriptionInputs()).toHaveLength(2);
    expect(document.activeElement).toBe(descriptionInputs()[1]);
  });

  it("ArrowDown/ArrowUp move focus between rows in the same column", () => {
    render(<Harness initialRows={[emptyLineGridRow(), emptyLineGridRow()]} />);
    const [row0, row1] = descriptionInputs();

    fireEvent.keyDown(row0, { key: "ArrowDown" });
    expect(document.activeElement).toBe(row1);

    fireEvent.keyDown(row1, { key: "ArrowUp" });
    expect(document.activeElement).toBe(row0);
  });

  it("clamps ArrowDown/ArrowUp at the grid's edges instead of leaving it", () => {
    render(<Harness initialRows={[emptyLineGridRow(), emptyLineGridRow()]} />);
    const [row0, row1] = descriptionInputs();

    fireEvent.keyDown(row1, { key: "ArrowDown" });
    expect(document.activeElement).toBe(row1);

    fireEvent.keyDown(row0, { key: "ArrowUp" });
    expect(document.activeElement).toBe(row0);
  });

  it("Escape reverts an in-progress edit to the value at focus time", () => {
    render(<Harness initialRows={[emptyLineGridRow({ description: "Original text" })]} />);
    const input = descriptionInputs()[0];

    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "Changed text" } });
    expect(descriptionInputs()[0].value).toBe("Changed text");

    fireEvent.keyDown(input, { key: "Escape" });

    expect(descriptionInputs()[0].value).toBe("Original text");
  });

  it("leaves a committed value alone when Escape is pressed without an edit in progress", () => {
    render(<Harness initialRows={[emptyLineGridRow({ description: "Steady" })]} />);
    const input = descriptionInputs()[0];

    fireEvent.keyDown(input, { key: "Escape" });

    expect(descriptionInputs()[0].value).toBe("Steady");
  });
});

// Regression for the FRw 1,000,065,000 incident: an amount cell swaps between a
// comma-formatted display and raw digits on focus. The old code called .select() to
// arm the overwrite synchronously inside onFocus, before that swap had committed to
// the DOM, so it selected the stale formatted text; once React then swapped in the raw
// value, the selection no longer covered the full field, and new input landed next to
// a surviving fragment of the old value instead of replacing it (65,000 in becoming
// 1,000,065,000). The fix (line-grid.tsx) moved the select() into a useEffect that
// runs after the raw value has committed. These tests overwrite a debit cell that
// already holds a value via both a full-value replace (fill) and real keystrokes,
// mirroring frontend/scripts/e2e-regression-amount-overwrite.ts.
describe("LineGrid amount cell overwrite (FRw 1,000,065,000 regression)", () => {
  it("replaces a stale formatted value instead of concatenating, on fill", async () => {
    const user = userEvent.setup();
    render(<Harness initialRows={[emptyLineGridRow({ debit: "10000" })]} />);
    const debitInput = screen.getByLabelText("Debit, row 1");
    const descInput = screen.getByLabelText("Description, row 1");

    expect(debitInput).toHaveValue("10,000");

    await user.click(debitInput);
    await user.clear(debitInput);
    await user.type(debitInput, "65000");
    await user.click(descInput); // blur -> reformats with thousand separators

    expect(debitInput).toHaveValue("65,000");
  });

  it("replaces a stale value instead of concatenating, when typed over after a fresh focus", async () => {
    const user = userEvent.setup();
    render(<Harness initialRows={[emptyLineGridRow({ debit: "42000" })]} />);
    const debitInput = screen.getByLabelText("Debit, row 1");
    const descInput = screen.getByLabelText("Description, row 1");

    await user.click(descInput); // focus elsewhere first, so the next click is a real transition
    await user.click(debitInput); // fresh focus -> the post-render effect selects the raw value
    // user.keyboard() types into whatever is currently focused and respects the live DOM
    // selection; user.type(element, text) re-focuses/repositions the caret itself first, which
    // would mask this regression by resetting the very selection the fix depends on.
    await user.keyboard("65000");
    await user.click(descInput); // blur -> reformats

    expect(debitInput).toHaveValue("65,000");
  });
});
