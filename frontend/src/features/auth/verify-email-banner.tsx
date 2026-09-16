"use client";

import { useTranslations } from "next-intl";
import { MailWarning } from "lucide-react";
import { Button } from "@/design/components/button";
import { useToast } from "@/design/components/toast";
import { useMe, useRequestEmailVerification } from "@/features/auth/hooks";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

/**
 * The only thing in the product that asks a user to verify their address.
 *
 * `POST /auth/email-verification/request` shipped in P1 with no caller: the mail goes out once
 * at signup, and a user who missed it or let the link expire had no way to ask for another —
 * and nothing ever told them it mattered. A banner is the whole affordance, so it sits in the
 * shell rather than on a settings page nobody visits to find out.
 *
 * It renders for exactly one state and is otherwise absent: a verified user, or a page with no
 * session, sees nothing. `already_verified` is still handled, because `me` can be a minute
 * stale and being told "already done" is a better answer than an error.
 */
export function VerifyEmailBanner() {
  const t = useTranslations("auth");
  const { data: me } = useMe();
  const requestVerification = useRequestEmailVerification();
  const toast = useToast();
  const showApiError = useApiErrorToast();

  if (!me || me.is_email_verified) return null;

  async function send() {
    try {
      const result = await requestVerification.mutateAsync();
      if (result.status === "already_verified") {
        toast.show({ title: t("verifyAlreadyDone"), tone: "success" });
        return;
      }
      toast.show({ title: t("verifySent"), description: t("verifySentBody"), tone: "success" });
    } catch (err) {
      showApiError(err, t("verifySendFailed"));
    }
  }

  return (
    // `print:hidden`, like every other piece of app chrome. A printed report carries a masthead
    // saying what it is and when it was run, and nothing else: a prompt to verify an email
    // address is addressed to the person at the screen, and it was landing above the company
    // name on every report anyone printed.
    <div
      className="flex items-center justify-between gap-3 border-b border-[var(--vinea-border)] bg-[var(--vinea-warning-soft)] px-4 py-2 print:hidden"
      data-testid="verify-email-banner"
    >
      <p className="flex items-center gap-2 text-xs text-[var(--vinea-ink)]">
        <MailWarning className="size-4 shrink-0" />
        {t("verifyBanner")}
      </p>
      <Button
        variant="secondary"
        onClick={send}
        disabled={requestVerification.isPending}
        className="h-7 shrink-0 px-2 text-xs"
      >
        {t("verifyBannerAction")}
      </Button>
    </div>
  );
}
