"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslations } from "next-intl";
import { Button } from "@/design/components/button";
import { Field, Input } from "@/design/components/input";
import { useLogin, useMe } from "@/features/auth/hooks";
import { useApiErrorToast } from "@/lib/use-api-error-toast";

const schema = z.object({
  email: z.string().email(),
  password: z.string().min(1, "Password is required"),
});
type FormValues = z.infer<typeof schema>;

export default function LoginPage() {
  const t = useTranslations("auth");
  const router = useRouter();
  const { data: me } = useMe();
  const login = useLogin();
  const showApiError = useApiErrorToast();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  useEffect(() => {
    if (me) router.replace("/");
  }, [me, router]);

  async function onSubmit(values: FormValues) {
    try {
      await login.mutateAsync(values);
      router.replace("/");
    } catch (err) {
      showApiError(err, "Sign in failed");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--vinea-surface-sunken)] px-4">
      <div className="w-full max-w-sm rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-8 shadow-[var(--elevation-2)]">
        <h1 className="font-display text-2xl font-semibold">{t("loginTitle")}</h1>
        <p className="mt-1 text-sm text-[var(--vinea-ink-muted)]">{t("loginSubtitle")}</p>

        <form onSubmit={handleSubmit(onSubmit)} className="mt-6 space-y-4">
          <Field label={t("email")} error={errors.email?.message}>
            <Input type="email" autoComplete="email" {...register("email")} />
          </Field>
          <Field label={t("password")} error={errors.password?.message}>
            <Input type="password" autoComplete="current-password" {...register("password")} />
          </Field>
          <Button type="submit" variant="primary" className="w-full" disabled={isSubmitting}>
            {isSubmitting ? t("signingIn") : t("signIn")}
          </Button>
        </form>
      </div>
    </div>
  );
}
