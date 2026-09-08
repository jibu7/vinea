import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
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
