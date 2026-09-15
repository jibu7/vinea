"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { Field, Input } from "@/design/components/input";
import { AuthCard, AuthOutcome } from "@/features/auth/auth-card";
import { useConfirmPasswordReset } from "@/features/auth/hooks";

/** Mirrors `PASSWORD_MIN_LENGTH` in `backend/app/schemas/auth.py`. The server is the
 * authority; this only saves a round trip to be told the obvious. */
const PASSWORD_MIN_LENGTH = 12;

const schema = z
  .object({
    new_password: z.string().min(PASSWORD_MIN_LENGTH),
    confirm: z.string(),
  })
  .refine((v) => v.new_password === v.confirm, {
    path: ["confirm"],
    message: "passwordsMustMatch",
  });
type FormValues = z.infer<typeof schema>;

/**
 * Set a new password with the token from a reset email.
 *
 * The token arrives as `?token=` and is never shown, stored or echoed — it goes straight back
 * to the server and is single-use there. A used or expired one answers 401, which is the
 * failure panel rather than a field error: there is nothing on this form the user could fix.
 *
 * On success the server clears the session cookies and revokes every other session, so the
 * only honest next step is signing in again — which is also the proof the reset worked.
 */
function ResetPasswordForm() {
  const t = useTranslations("auth");
  const token = useSearchParams().get("token") ?? "";
  const confirmReset = useConfirmPasswordReset();
  const [done, setDone] = useState(false);
  const [rejected, setRejected] = useState(false);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  if (!token) {
    return (
      <AuthOutcome title={t("missingToken")} body={t("missingTokenBody")}>
        <Link href="/forgot-password" className="block">
          <Button variant="secondary" className="w-full">
            {t("sendResetLink")}
          </Button>
        </Link>
      </AuthOutcome>
    );
  }

  if (done) {
    return (
      <AuthOutcome title={t("resetDone")} body={t("resetDoneBody")}>
        <Link href="/login" className="block">
          <Button variant="primary" className="w-full">
            {t("signIn")}
          </Button>
        </Link>
      </AuthOutcome>
    );
  }

  if (rejected) {
    return (
      <AuthOutcome title={t("resetFailed")} body={t("resetFailedBody")}>
        <Link href="/forgot-password" className="block">
          <Button variant="primary" className="w-full">
            {t("sendResetLink")}
          </Button>
        </Link>
      </AuthOutcome>
    );
  }

  async function onSubmit(values: FormValues) {
    try {
      await confirmReset.mutateAsync({ token, new_password: values.new_password });
      setDone(true);
    } catch {
      // A spent or expired link. Nothing on this form can fix it, so the whole panel changes
      // rather than a field growing an error the user cannot act on.
      setRejected(true);
    }
  }

  return (
    <AuthCard title={t("resetTitle")} subtitle={t("resetSubtitle")}>
      <form onSubmit={handleSubmit(onSubmit)} className="mt-6 space-y-4">
        <Field label={t("newPassword")} error={errors.new_password?.message}>
          <Input
            type="password"
            autoComplete="new-password"
            autoFocus
            {...register("new_password")}
          />
        </Field>
        <Field
          label={t("confirmPassword")}
          error={errors.confirm?.message === "passwordsMustMatch" ? t("passwordsMustMatch") : errors.confirm?.message}
        >
          <Input type="password" autoComplete="new-password" {...register("confirm")} />
        </Field>
        <Button type="submit" variant="primary" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? t("settingPassword") : t("setPassword")}
        </Button>
      </form>
    </AuthCard>
  );
}

export default function ResetPasswordPage() {
  // `useSearchParams` suspends during prerender; Next requires the boundary.
  return (
    <Suspense>
      <ResetPasswordForm />
    </Suspense>
  );
}
