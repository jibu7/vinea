import { getRequestConfig } from "next-intl/server";

/** Single locale at launch (en) — i18n plumbing is in from day 1 per the P3 plan. */
export default getRequestConfig(async () => {
  const locale = "en";
  return {
    locale,
    messages: (await import(`./messages/${locale}.json`)).default,
  };
});
