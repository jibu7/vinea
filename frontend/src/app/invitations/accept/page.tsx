"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { Field, Input } from "@/design/components/input";
import { AuthCard, AuthOutcome } from "@/features/auth/auth-card";
import { useAcceptInvitation } from "@/features/auth/hooks";

/** Mirrors `PASSWORD_MIN_LENGTH` in `backend/app/schemas/auth.py`. */
const PASSWORD_MIN_LENGTH = 12;

const schema = z.object({
  full_name: z.string().min(2),
  password: z.string().min(PASSWORD_MIN_LENGTH),
});
type FormValues = z.infer<typeof schema>;

/**
 * Accept an invitation: pick a name and a password, and you are in.
 *
 * **This is the page whose absence made the endpoint unreachable.** `POST /invitations/accept`
 * has existed since P1 with no caller at all — an invited user had no page to accept on, so
 * the only way into a tenancy was the signup form, which creates a *new* company rather than
 * joining the one that invited you.
 *
 * The response is a session, not a confirmation, and that is the point: an invited user has no
 * password until this form gives them one, so sending them to `/login` afterwards would ask
 * for something they have only just set and could as easily be handed straight through. They
 * land in the company that invited them.
 */
function AcceptInvitation() {
  const t = useTranslations("auth");
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const company = params.get("company");
  const accept = useAcceptInvitation();
  const [rejected, setRejected] = useState(false);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  if (!token) {
    return <AuthOutcome title={t("missingToken")} body={t("missingTokenBody")} />;
  }

  if (rejected) {
    return (
      <AuthOutcome title={t("acceptFailed")} body={t("acceptFailedBody")}>
        <Link href="/login" className="block">
          <Button variant="secondary" className="w-full">
            {t("backToSignIn")}
          </Button>
        </Link>
      </AuthOutcome>
    );
  }

  async function onSubmit(values: FormValues) {
    try {
      await accept.mutateAsync({ token, ...values });
      router.replace("/");
    } catch {
      // Expired, already used, or revoked while the page was open. All three are "this
      // invitation no longer works", and none of them is something this form can fix.
      setRejected(true);
    }
  }

  return (
    <AuthCard
      title={company ? t("acceptTitle", { company }) : t("acceptTitleFallback")}
      subtitle={t("acceptSubtitle")}
    >
      <form onSubmit={handleSubmit(onSubmit)} className="mt-6 space-y-4">
        <Field label={t("fullName")} error={errors.full_name?.message}>
          <Input autoComplete="name" autoFocus {...register("full_name")} />
        </Field>
        <Field label={t("password")} error={errors.password?.message}>
          <Input type="password" autoComplete="new-password" {...register("password")} />
        </Field>
        <Button type="submit" variant="primary" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? t("accepting") : t("acceptInvitation")}
        </Button>
      </form>
    </AuthCard>
  );
}

export default function AcceptInvitationPage() {
  return (
    <Suspense>
      <AcceptInvitation />
    </Suspense>
  );
}
