import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import messages from "@/i18n/messages/en.json";
import { QueryState, queryErrorMessage } from "./query-state";

/**
 * The one distinction this component exists to make: **"no rows" is not "the request failed"**.
 *
 * P4 shipped six defects of that shape (rule 13) and P6 met two more — step 6's F5 and step
 * 8's F-3 were the same defect twice. Both were found by opening a screen, not by a test, so
 * step 9 built the component once and swept every listing, report and enquiry onto it. These
 * assertions are what stops the sweep from being undone one screen at a time.
 */
function show(ui: React.ReactElement) {
  return render(
    <NextIntlClientProvider locale="en-GB" messages={messages}>
      {ui}
    </NextIntlClientProvider>,
  );
}

const idle = { isError: false, isLoading: false };

describe("QueryState", () => {
  it("says what the service said when the query failed", () => {
    show(
      <QueryState
        query={{ isError: true, isLoading: false, error: new Error("Period 2026-03 is closed") }}
        isEmpty
        empty="Nothing to report."
      />,
    );
    expect(screen.getByTestId("query-state-error")).toHaveTextContent("Period 2026-03 is closed");
    // And it is *not* the empty line, which is the whole point.
    expect(screen.queryByText("Nothing to report.")).toBeNull();
    expect(screen.queryByTestId("query-state-empty")).toBeNull();
  });

  it("falls back to its own words only when the failure carried none", () => {
    // A dropped connection has no response body and therefore no message. Saying "the server
    // did not say why" is still a different claim from "there is nothing here".
    show(<QueryState query={{ isError: true, isLoading: false, error: {} }} isEmpty />);
    expect(screen.getByTestId("query-state-error")).toHaveTextContent(
      messages.common.queryState.failed,
    );
  });

  it("prefers the failure to the loading line", () => {
    // A screen that checks isLoading first shows "Loading…" for ever on a query that failed.
    show(
      <QueryState
        query={{ isError: true, isLoading: true, error: new Error("boom") }}
        isEmpty={false}
      />,
    );
    expect(screen.getByTestId("query-state-error")).toHaveTextContent("boom");
  });

  it("shows the empty line when the query succeeded with no rows", () => {
    show(<QueryState query={idle} isEmpty empty="No sales orders yet." />);
    expect(screen.getByTestId("query-state-empty")).toHaveTextContent("No sales orders yet.");
  });

  it("renders nothing when there are rows to render", () => {
    const { container } = show(<QueryState query={idle} isEmpty={false} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names its testids from the prefix, so one screen can carry two", () => {
    show(<QueryState query={idle} isEmpty testId="preview" />);
    expect(screen.getByTestId("preview-empty")).toBeInTheDocument();
  });
});

describe("queryErrorMessage", () => {
  it("ignores a blank message rather than rendering an empty line", () => {
    expect(queryErrorMessage({ message: "   " }, "fallback")).toBe("fallback");
    expect(queryErrorMessage(null, "fallback")).toBe("fallback");
    expect(queryErrorMessage(new Error("refused"), "fallback")).toBe("refused");
  });
});

/**
 * The sweep itself, held in place.
 *
 * A component built once is only a fix while the screens keep using it. The failure mode is
 * the cheap one: somebody adds a listing, writes `{q.isLoading ? "Loading…" : "Nothing to
 * report."}` because that is what the file next door used to look like, and the product grows
 * a screen that cannot tell a broken query from an empty one — which is exactly how P6 got two
 * of them. So the old idiom is banned by name.
 */
describe("no screen renders its own loading-or-empty line", () => {
  const SRC = join(process.cwd(), "src");

  function tsxFiles(dir: string): string[] {
    const out: string[] = [];
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) out.push(...tsxFiles(full));
      else if (entry.endsWith(".tsx") && !entry.includes(".test.")) out.push(full);
    }
    return out;
  }

  it("finds the screens it claims to check", () => {
    expect(tsxFiles(SRC).length).toBeGreaterThan(40);
  });

  it("has no `<query>.isLoading ? … : …` left anywhere", () => {
    const offenders: string[] = [];
    for (const file of tsxFiles(SRC)) {
      const source = readFileSync(file, "utf8");
      // The exact shape the sweep removed: a ternary on a query's own isLoading, choosing
      // between two pieces of copy. `QueryState` takes that decision now, and it takes the
      // error branch with it.
      if (/\w+\.isLoading \?[^?]*?:/.test(source)) {
        offenders.push(file.replace(SRC, "src"));
      }
    }
    expect(
      offenders,
      "these screens decide loading-vs-empty themselves, so they cannot report a failure:\n" +
        offenders.join("\n"),
    ).toEqual([]);
  });
});
