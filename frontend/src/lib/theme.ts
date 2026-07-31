/**
 * Theme preference — light / dark / follow the system.
 *
 * The `.dark` design tokens have existed in `globals.css` since the visual pass
 * and `tailwind.config.ts` is already `darkMode: ["class"]`; the only thing
 * missing was something to put the class on `<html>`. This is that, hand-rolled
 * rather than `next-themes`, for the same reason `app/auth/` and `lib/sse.ts`
 * are hand-rolled: it is forty lines against a dependency (CLAUDE.md rule 11).
 *
 * Three states, not two. A two-state toggle silently discards "follow the OS",
 * which is the correct default and unreachable again once a user has clicked.
 *
 * The store is module-level rather than React state because `<html>`'s class is
 * the real source of truth and it is set by an inline script *before* React
 * mounts (see `layout.tsx`) — a `useState` initialiser would disagree with the
 * DOM on the first render and hydrate mismatched.
 */

export type ThemeChoice = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "theme";
export const THEME_ORDER: readonly ThemeChoice[] = ["system", "light", "dark"];

function isChoice(value: unknown): value is ThemeChoice {
  return value === "system" || value === "light" || value === "dark";
}

/** The stored preference, or `"system"` when nothing is stored or readable. */
export function readChoice(): ThemeChoice {
  if (typeof window === "undefined") return "system";
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isChoice(raw) ? raw : "system";
  } catch {
    return "system"; // private mode / storage disabled
  }
}

export function systemTheme(): ResolvedTheme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function resolve(choice: ThemeChoice): ResolvedTheme {
  return choice === "system" ? systemTheme() : choice;
}

/** The next choice in the cycle: system → light → dark → system. */
export function nextChoice(choice: ThemeChoice): ThemeChoice {
  return THEME_ORDER[(THEME_ORDER.indexOf(choice) + 1) % THEME_ORDER.length];
}

const listeners = new Set<() => void>();

function notify(): void {
  for (const fn of listeners) fn();
}

/** Put the resolved theme on `<html>`. The one place the class is written. */
export function applyChoice(choice: ThemeChoice): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", resolve(choice) === "dark");
}

export function setChoice(choice: ThemeChoice): void {
  try {
    // "system" is stored as an absent key, not as the string: that way a user
    // who never touches the toggle and one who cycles back to system end up in
    // the same state, and the pre-paint script has one case fewer.
    if (choice === "system") window.localStorage.removeItem(THEME_STORAGE_KEY);
    else window.localStorage.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    // Storage unavailable: the choice still applies for this page.
  }
  applyChoice(choice);
  notify();
}

/**
 * `useSyncExternalStore` plumbing. Subscribes to our own `setChoice` calls *and*
 * to the OS preference, so a machine switching to dark at sunset moves a
 * `system` page with it rather than waiting for a reload.
 */
export function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const onMedia = () => {
    if (readChoice() === "system") applyChoice("system");
    onChange();
  };
  media.addEventListener("change", onMedia);
  // Another tab writing the key: `storage` fires only in the *other* tabs,
  // which is exactly the ones that need to catch up.
  const onStorage = (e: StorageEvent) => {
    if (e.key === THEME_STORAGE_KEY || e.key === null) {
      applyChoice(readChoice());
      onChange();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(onChange);
    media.removeEventListener("change", onMedia);
    window.removeEventListener("storage", onStorage);
  };
}

/**
 * The script that runs before first paint, inlined into `<head>`.
 *
 * Without it the page renders light, then flips to dark once React mounts —
 * a full-screen flash on every navigation for every dark-mode user. It is a
 * string because it must execute before hydration, and it is deliberately tiny:
 * read one key, set one class.
 */
export const THEME_INIT_SCRIPT = `(function(){try{var c=localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)});var d=c==="dark"||(c!=="light"&&matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d);}catch(e){}})();`;
