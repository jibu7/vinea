import type { AgeingBucketInput } from "./types";

/**
 * The bucket ladder the service will accept: start at day 0, contiguous, last one open.
 *
 * The editor owns `from_days` entirely — each row starts the day after the previous row ends,
 * and the last row's `to_days` is always null. Only the label and the intermediate `to_days`
 * are the operator's, so an invalid ladder cannot be typed in the first place; the service's
 * `_validate_buckets` is the authority, this keeps the form from ever reaching it with a gap.
 */
export function normaliseBuckets(buckets: AgeingBucketInput[]): AgeingBucketInput[] {
  return buckets.map((bucket, index) => ({
    label: bucket.label,
    from_days: index === 0 ? 0 : (buckets[index - 1].to_days ?? 0) + 1,
    to_days: index === buckets.length - 1 ? null : (bucket.to_days ?? 0),
  }));
}

/** Appends an open-ended bucket, closing the one that was previously last. */
export function appendBucket(buckets: AgeingBucketInput[], label: (boundary: number) => string) {
  const last = buckets[buckets.length - 1];
  const boundary = last ? last.from_days + 30 : 30;
  const closed = buckets.map((bucket, i) =>
    i === buckets.length - 1 ? { ...bucket, to_days: boundary } : bucket,
  );
  return normaliseBuckets([
    ...closed,
    { label: label(boundary + 1), from_days: boundary + 1, to_days: null },
  ]);
}

export function removeBucket(buckets: AgeingBucketInput[], index: number) {
  return buckets.length <= 1 ? buckets : normaliseBuckets(buckets.filter((_, i) => i !== index));
}
