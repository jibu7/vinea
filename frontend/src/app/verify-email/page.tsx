"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { AuthCard, AuthOutcome } from "@/features/auth/auth-card";
import { useConfirmEmailVerification } from "@/features/auth/hooks";

/**
 * The landing page for the link in a verification email.
 *
 * It confirms on arrival rather than asking the user to press anything: they already acted, by
 * clicking the link in their mail. There is no second decision to make here, and a button
 * would only be another thing to get wrong.
 *
 * Verification needs **no session** — the token identifies the user — so this sits outside the
 * shell like the reset screens. A signed-out user following the link from their phone's mail
 * app gets the same result as a signed-in one.
 */
function VerifyEmail() {
  const t = useTranslations("auth");
  const token = useSearchParams().get("token") ?? "";
  const confirm = useConfirmEmailVerification();
  const [state, setState] = useState<"working" | "done" | "failed">("working");
  // React 18 mounts effects twice in development. The token is single-use, so a second call
  // would fail and flip a genuine success to an error panel.
  const attempted = useRef(false);

  useEffect(() => {
    if (!token || attempted.current) return;
    attempted.current = true;
    confirm
      .mutateAsync(token)
      .then(() => setState("done"))
      .catch(() => setState("failed"));
  }, [token, confirm]);

  if (!token) {
    return <AuthOutcome title={t("missingToken")} body={t("missingTokenBody")} />;
  }
  if (state === "working") {
    return <AuthCard title={t("verifyTitle")} subtitle={t("verifyWorking")} />;
  }
  if (state === "done") {
    return (
      <AuthOutcome title={t("verifyDone")} body={t("verifyDoneBody")}>
        <Link href="/" className="block">
          <Button variant="primary" className="w-full">
            {t("continueToVinea")}
          </Button>
        </Link>
      </AuthOutcome>
    );
  }
  return (
    <AuthOutcome title={t("verifyFailed")} body={t("verifyFailedBody")}>
      <Link href="/" className="block">
        <Button variant="secondary" className="w-full">
          {t("continueToVinea")}
        </Button>
      </Link>
    </AuthOutcome>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense>
      <VerifyEmail />
    </Suspense>
  );
}
