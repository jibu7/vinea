"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Moon, Sun } from "lucide-react";
import { Button } from "./button";

/** Reads/writes `data-theme` on <html>. Actual theme persistence lands with auth/session in step 3. */
export function ThemeToggle() {
  const t = useTranslations("common");
  const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    const stored = document.documentElement.getAttribute("data-theme");
    if (stored === "dark") setTheme("dark");
  }, []);

  function toggle() {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
  }

  return (
    <Button variant="ghost" size="sm" onClick={toggle} aria-label={t("toggleTheme")}>
      {theme === "light" ? <Moon className="size-4" /> : <Sun className="size-4" />}
    </Button>
  );
}
