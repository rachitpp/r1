"use client";

/**
 * The current theme choice and its resolved value, kept in sync with `<html>`.
 *
 * `useSyncExternalStore` rather than `useState` because the class on `<html>` is
 * written by a pre-paint script (`THEME_INIT_SCRIPT`) and can also change from
 * another tab or from the OS — three writers that React state cannot see. The
 * server snapshot is `"system"`, which is what the markup is rendered against.
 */

import { useCallback, useSyncExternalStore } from "react";

import {
  type ResolvedTheme,
  type ThemeChoice,
  nextChoice,
  readChoice,
  resolve,
  setChoice,
  subscribe,
} from "@/lib/theme";

export interface Theme {
  choice: ThemeChoice;
  /** What `choice` actually renders as right now. */
  resolved: ResolvedTheme;
  /** True once mounted; false during SSR and the first render. */
  ready: boolean;
  set: (choice: ThemeChoice) => void;
  cycle: () => void;
}

export function useTheme(): Theme {
  const choice = useSyncExternalStore(
    subscribe,
    readChoice,
    () => "system" as ThemeChoice,
  );
  // Resolving needs `matchMedia`, so it is a separate subscription rather than
  // derived from `choice` — otherwise a `system` page would never re-render
  // when the OS flipped, because `choice` did not change.
  const resolved = useSyncExternalStore(
    subscribe,
    () => resolve(readChoice()),
    () => "light" as ResolvedTheme,
  );
  const ready = useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );

  const cycle = useCallback(() => setChoice(nextChoice(readChoice())), []);
  return { choice, resolved, ready, set: setChoice, cycle };
}
