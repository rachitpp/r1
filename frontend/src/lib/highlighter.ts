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

export const LIGHT_THEME = "github-light";
export const DARK_THEME = "github-dark";

let instance: Promise<HighlighterCore> | null = null;

export function getHighlighter(): Promise<HighlighterCore> {
  instance ??= createHighlighterCore({
    themes: [
      import("shiki/dist/themes/github-light.mjs"),
      import("shiki/dist/themes/github-dark.mjs"),
    ],
    langs: [import("shiki/dist/langs/python.mjs")],
    engine: createJavaScriptRegexEngine({ forgiving: true }),
  });
  return instance;
}
