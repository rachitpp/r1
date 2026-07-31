"use client";

/**
 * Header theme control: one button cycling system → light → dark.
 *
 * The icon shows the *current* choice rather than what a click will do — a
 * toggle that displays its own destination is the classic ambiguity here, and
 * with three states there is no defensible way to draw "the next one".
 */

import { Monitor, Moon, Sun } from "lucide-react";

import { useTheme } from "@/hooks/use-theme";

const LABEL = {
  system: "Theme: follow system",
  light: "Theme: light",
  dark: "Theme: dark",
} as const;

const ICON = { system: Monitor, light: Sun, dark: Moon } as const;

export function ThemeToggle() {
  const { choice, ready, cycle } = useTheme();
  // Before mount the stored choice is unknown, so render the neutral system
  // icon: guessing would flash the wrong glyph for anyone who set a preference.
  const Icon = ICON[ready ? choice : "system"];

  return (
    <button
      type="button"
      onClick={cycle}
      title={LABEL[choice]}
      aria-label={LABEL[choice]}
      className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <Icon className="size-4" />
    </button>
  );
}
