"use client";

import type { SelectOption } from "@/design/components/select";
import { useAccounts, useBranches, useCurrencies, useProjects, useTaxCodes } from "./hooks";

/** One place to fetch every master a document workspace's line cells need. */
export function useGLLookups() {
  const accounts = useAccounts();
  const branches = useBranches();
  const projects = useProjects();
  const currencies = useCurrencies();
  const taxCodes = useTaxCodes();

  return {
    accounts,
    branches,
    projects,
    currencies,
    taxCodes,
    isLoading:
      accounts.isLoading || branches.isLoading || projects.isLoading || currencies.isLoading || taxCodes.isLoading,
  };
}

export function toOptions<T extends { id: number }>(items: T[] | undefined, label: (item: T) => string): SelectOption[] {
  return (items ?? []).map((item) => ({ value: String(item.id), label: label(item) }));
}

export function byId<T extends { id: number }>(items: T[] | undefined): Map<number, T> {
  return new Map((items ?? []).map((item) => [item.id, item]));
}
