/**
 * Shaping helpers for the §18.2 module rollup.
 *
 * The endpoint returns a flat edge list because that is what SQL produces
 * cheaply and what a diagram would want. A reader looking at one module wants
 * the two directions separately — what it leans on, and what leans on it — so
 * the pivot happens here rather than in the component, where it would not be
 * testable.
 *
 * Pure functions only. Nothing here fetches or renders.
 */

import type { ModuleEdge } from "@/lib/api";

/** One end of a dependency, with every edge kind between the pair merged. */
export interface ModuleLink {
  path: string;
  /** `calls`, `imports`, `extends` — in descending weight order. */
  kinds: { kind: string; weight: number }[];
  /** Total symbol-level edges across all kinds. Drives ordering. */
  weight: number;
}

function pivot(
  edges: ModuleEdge[],
  path: string,
  side: "from_path" | "to_path",
): ModuleLink[] {
  const other = side === "from_path" ? "to_path" : "from_path";
  const byPath = new Map<string, ModuleLink>();
  for (const e of edges) {
    if (e[side] !== path) continue;
    const key = e[other];
    let link = byPath.get(key);
    if (!link) {
      link = { path: key, kinds: [], weight: 0 };
      byPath.set(key, link);
    }
    link.kinds.push({ kind: e.kind, weight: e.weight });
    link.weight += e.weight;
  }
  const links = [...byPath.values()];
  for (const l of links) l.kinds.sort((a, b) => b.weight - a.weight || a.kind.localeCompare(b.kind));
  // Heaviest coupling first; path as the tiebreaker so the order is stable
  // rather than dependent on the order SQL happened to return rows in.
  links.sort((a, b) => b.weight - a.weight || a.path.localeCompare(b.path));
  return links;
}

/** What `path` leans on: edges leaving it. */
export function dependenciesOf(edges: ModuleEdge[], path: string): ModuleLink[] {
  return pivot(edges, path, "from_path");
}

/** What leans on `path`: edges arriving at it. */
export function dependentsOf(edges: ModuleEdge[], path: string): ModuleLink[] {
  return pivot(edges, path, "to_path");
}

/**
 * Fraction of `max`, clamped to 0..1, for a bar width.
 *
 * A zero or negative max returns 0 rather than dividing — a repo whose modules
 * are all fan-in 0 (no resolved cross-module edges) is a real case, not an
 * error, and it should render flat bars instead of `NaN%`.
 */
export function shareOf(value: number, max: number): number {
  if (max <= 0) return 0;
  return Math.max(0, Math.min(1, value / max));
}

/** `httpx/_client.py` → `{ dir: "httpx/", file: "_client.py" }` for two-tone display. */
export function splitModulePath(path: string): { dir: string; file: string } {
  const cut = path.lastIndexOf("/");
  return cut < 0
    ? { dir: "", file: path }
    : { dir: path.slice(0, cut + 1), file: path.slice(cut + 1) };
}

/**
 * The question behind a module's "Ask about this" link.
 *
 * Names the module and both directions the rollup just showed, so the agent
 * starts from the structure the reader is looking at rather than rediscovering
 * it with search calls it does not have the budget for.
 */
export function moduleQuestion(path: string): string {
  return (
    `What is \`${path}\` responsible for, and how does it relate to the ` +
    `modules that depend on it?`
  );
}
