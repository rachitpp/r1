/**
 * Shiki singleton — fine-grained bundle (Phase 5 bundle-size discipline).
 *
 * v1 indexes Python repos only (CLAUDE.md scope), so the full shiki bundle
 * (~all grammars + oniguruma wasm) would be dead weight. This loads exactly:
 * the core, the JavaScript regex engine (no wasm), the Python grammar, and one
 * light + one dark theme. Created once per browser session and shared by every
 * viewer instance.
 */

import { createHighlighterCore, type HighlighterCore } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";

/**
 * `vitesse-light`, not `github-light`. GitHub's light theme is a cool palette
 * (blue / purple / crimson) on a pure-white ground; the viewer's card is warm
 * paper, and cool syntax on a warm card reads as a foreign object pasted into
 * the page — across 44% of the screen. Vitesse is warm-neutral and sits on the
 * palette without being tinted by hand.
 */
export const LIGHT_THEME = "vitesse-light";
export const DARK_THEME = "vitesse-dark";

let instance: Promise<HighlighterCore> | null = null;

export function getHighlighter(): Promise<HighlighterCore> {
  instance ??= createHighlighterCore({
    themes: [
      import("shiki/dist/themes/vitesse-light.mjs"),
      import("shiki/dist/themes/vitesse-dark.mjs"),
    ],
    langs: [import("shiki/dist/langs/python.mjs")],
    engine: createJavaScriptRegexEngine({ forgiving: true }),
  });
  return instance;
}
