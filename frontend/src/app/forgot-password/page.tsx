"use client";

import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { Field, Input } from "@/design/components/input";
import { AuthCard, AuthOutcome } from "@/features/auth/auth-card";
import { useRequestPasswordReset } from "@/features/auth/hooks";

const schema = z.object({ email: z.string().email() });
type FormValues = z.infer<typeof schema>;

/**
 * Ask for a password-reset mail.
 *
 * **The screen shows the same thing whatever happens**, and that is the design rather than a
 * shortcut: `POST /auth/password-reset/request` answers 202 for an address it has never seen,
 * because telling a stranger which addresses have accounts is an account-enumeration oracle.
 * A screen that said "no such user" would hand back exactly what the endpoint refuses to.
 *
 * So there is no error path to render here. Even a failed request lands on the same panel.
 */
export default function ForgotPasswordPage() {
  const t = useTranslations("auth");
  const requestReset = useRequestPasswordReset();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting, isSubmitSuccessful },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: FormValues) {
    // Deliberately not surfacing a failure: see the note above. The user is told their mail is
    // on its way either way, which is true of every address that has an account.
    await requestReset.mutateAsync(values.email).catch(() => undefined);
  }

  if (isSubmitSuccessful) {
    return (
      <AuthOutcome title={t("resetRequested")} body={t("resetRequestedBody")}>
        <Link href="/login" className="block">
          <Button variant="secondary" className="w-full">
            {t("backToSignIn")}
          </Button>
        </Link>
      </AuthOutcome>
    );
  }

  return (
    <AuthCard title={t("forgotTitle")} subtitle={t("forgotSubtitle")}>
      <form onSubmit={handleSubmit(onSubmit)} className="mt-6 space-y-4">
        <Field label={t("email")} error={errors.email?.message}>
          <Input type="email" autoComplete="email" autoFocus {...register("email")} />
        </Field>
        <Button type="submit" variant="primary" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? t("sending") : t("sendResetLink")}
        </Button>
        <Link
          href="/login"
          className="block text-center text-xs text-[var(--vinea-ink-muted)] underline"
        >
          {t("backToSignIn")}
        </Link>
      </form>
    </AuthCard>
  );
}
