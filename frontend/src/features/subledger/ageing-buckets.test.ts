import { describe, expect, it } from "vitest";
import { appendBucket, normaliseBuckets, removeBucket } from "./ageing-buckets";
import type { AgeingBucketInput } from "./types";

const ladder: AgeingBucketInput[] = [
  { label: "Current", from_days: 0, to_days: 30 },
  { label: "31 - 60", from_days: 31, to_days: 60 },
  { label: "60+", from_days: 61, to_days: null },
];

/** `from_days` is derived, never entered — the screen renders it as text for that reason.
 * These pin the derivation the service's contiguity rule depends on. */
describe("normaliseBuckets", () => {
  it("always starts the first bucket at day 0, whatever was passed in", () => {
    const out = normaliseBuckets([{ label: "x", from_days: 99, to_days: 30 }, ...ladder.slice(1)]);
    expect(out[0].from_days).toBe(0);
  });

  it("starts each bucket the day after the previous one ends", () => {
    const edited = ladder.map((b, i) => (i === 0 ? { ...b, to_days: 45 } : b));

    const out = normaliseBuckets(edited);

    expect(out[1].from_days).toBe(46);
    expect(out.map((b) => [b.from_days, b.to_days])).toEqual([[0, 45], [46, 60], [61, null]]);
  });

  it("leaves only the last bucket open-ended", () => {
    const out = normaliseBuckets(ladder.map((b) => ({ ...b, to_days: b.to_days ?? 120 })));

    expect(out[out.length - 1].to_days).toBeNull();
    expect(out.slice(0, -1).every((b) => b.to_days !== null)).toBe(true);
  });

  it("keeps the operator's labels untouched", () => {
    expect(normaliseBuckets(ladder).map((b) => b.label)).toEqual(["Current", "31 - 60", "60+"]);
  });
});

describe("appendBucket", () => {
  it("closes the previously open bucket and opens the new one", () => {
    const out = appendBucket(ladder, (b) => `${b}+`);

    expect(out).toHaveLength(4);
    expect(out[2].to_days).not.toBeNull();
    expect(out[3].to_days).toBeNull();
    expect(out[3].from_days).toBe((out[2].to_days ?? 0) + 1);
  });
});

describe("removeBucket", () => {
  it("re-derives the ladder so no gap is left behind", () => {
    const out = removeBucket(ladder, 1);

    expect(out.map((b) => [b.from_days, b.to_days])).toEqual([[0, 30], [31, null]]);
  });

  it("refuses to remove the only bucket", () => {
    const single = [ladder[2]];
    expect(removeBucket(single, 0)).toEqual(single);
  });
});
