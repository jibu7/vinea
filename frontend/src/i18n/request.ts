import { getRequestConfig } from "next-intl/server";

/** Single locale at launch (en-GB — day/month/year dates match Rwanda convention). */
export default getRequestConfig(async () => {
  const locale = "en-GB";
  return {
    locale,
    messages: (await import(`./messages/en.json`)).default,
  };
});
