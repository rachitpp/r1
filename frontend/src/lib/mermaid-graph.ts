/**
 * The §18.2 module rollup, serialised as mermaid (FEATURE-IDEAS 3.3).
 *
 * The `edges` table already *is* a graph, so this is a serialisation and not a
 * computation — the same data the Architecture panel lists, drawn instead of
 * enumerated. A picture answers "what shape is this codebase" in one look; the
 * list answers "what does this module touch" in one click. They are the same
 * rollup and neither costs a model call.
 *
 * Pure functions only. Nothing here renders or touches mermaid itself, which is
 * what keeps the escaping and the top-N rules testable without a DOM.
 */

import type { ModuleEdge, ModuleNode } from "@/lib/api-types";

/**
 * Modules drawn by default.
 *
 * FEATURE-IDEAS 3.3 names the failure mode directly: "big graphs render into
 * hairballs". Twelve is where httpx still reads as one hub with a tail rather
 * than a ball of string; the panel beside it remains the way to see all of them.
 */
export const DIAGRAM_MAX_NODES = 12;

/**
 * Arrows drawn, strongest coupling first.
 *
 * Twelve modules can carry 45 resolved edges — drawn in full that is a ball of
 * string in which no structure is visible at all, which was the first version
 * of this and the reason for the cap. Keeping the heaviest 18 shows the
 * backbone; the rest are counted in the caption and remain exact in the list.
 */
export const DIAGRAM_MAX_EDGES = 18;

/** Past this many arrows the per-edge weight labels are more noise than signal. */
const EDGE_LABEL_LIMIT = 14;

/** Mermaid ids are positional (`m0`, `m1`) so no path can break the syntax. */
export interface DiagramNode {
  id: string;
  path: string;
  label: string;
  fanIn: number;
}

export interface Diagram {
  /** The `graph LR …` source, ready for `mermaid.render`. */
  source: string;
  /** Lookup for click-through: mermaid node id → the module it stands for. */
  byId: Map<string, DiagramNode>;
  /** Modules omitted by the top-N cap. */
  omitted: number;
  /** Edges not drawn: endpoint cut by the node cap, or weaker than the edge cap. */
  hiddenEdges: number;
  /** Arrows actually in `source`. */
  edgesDrawn: number;
}

/** Trailing `n` path segments: `("a/b/c.py", 2)` → `"b/c.py"`. */
function tail(path: string, n: number): string {
  return path.split("/").slice(-n).join("/");
}

/**
 * Label text for each node: the shortest suffix that stays unambiguous.
 *
 * Full paths make every box the same width and the diagram unreadable, so the
 * filename alone is the default. But flask has `src/flask/app.py` *and*
 * `src/flask/sansio/app.py`, and two boxes both reading `app.py` is worse than
 * a long label — it is a wrong one. Any filename claimed by more than one kept
 * module grows a directory until it is distinct.
 *
 * `__init__.py` is always qualified: on its own it names no module at all.
 */
function labelsFor(paths: string[]): Map<string, string> {
  const labels = new Map<string, string>();
  const claims = new Map<string, string[]>();
  for (const p of paths) {
    const base = tail(p, 1);
    const key = base === "__init__.py" ? tail(p, 2) : base;
    claims.set(key, [...(claims.get(key) ?? []), p]);
  }
  for (const [key, owners] of claims) {
    if (owners.length === 1) {
      labels.set(owners[0], key);
      continue;
    }
    // Lengthen every colliding path together until they differ, so the labels
    // stay comparable rather than one growing and the others not.
    for (let depth = 2; depth <= 6; depth += 1) {
      const grown = owners.map((p) => tail(p, depth));
      if (new Set(grown).size === owners.length || depth === 6) {
        owners.forEach((p, i) => labels.set(p, grown[i]));
        break;
      }
    }
  }
  return labels;
}

/**
 * Quote a label for mermaid.
 *
 * Mermaid takes `["…"]` for arbitrary text, which covers everything a Python
 * path can contain except a literal double quote — impossible in a POSIX-ish
 * repo path but cheap to neutralise, and a stray quote would otherwise end the
 * label and corrupt every line after it. `#quot;` is mermaid's own entity form.
 */
function quote(text: string): string {
  return `"${text.replace(/"/g, "#quot;")}"`;
}

/**
 * Build the diagram for a rollup.
 *
 * Nodes are the top `max` by fan-in — the same ranking the panel uses, so the
 * two views agree about what matters. Ties break on path so a repo does not
 * redraw differently between requests that returned rows in another order.
 */
export function buildDiagram(
  nodes: ModuleNode[],
  edges: ModuleEdge[],
  max: number = DIAGRAM_MAX_NODES,
  maxEdges: number = DIAGRAM_MAX_EDGES,
): Diagram {
  const ranked = [...nodes].sort(
    (a, b) => b.fan_in - a.fan_in || a.path.localeCompare(b.path),
  );
  const kept = ranked.slice(0, Math.max(0, max));

  const labels = labelsFor(kept.map((n) => n.path));
  const byPath = new Map<string, DiagramNode>();
  const byId = new Map<string, DiagramNode>();
  kept.forEach((n, i) => {
    const node: DiagramNode = {
      id: `m${i}`,
      path: n.path,
      label: labels.get(n.path) ?? n.path,
      fanIn: n.fan_in,
    };
    byPath.set(n.path, node);
    byId.set(node.id, node);
  });

  // Merge the per-kind rows into one arrow per ordered pair: three parallel
  // arrows labelled calls/imports/extends between the same two boxes is noise,
  // and the panel is where the breakdown belongs.
  const pairs = new Map<string, { from: DiagramNode; to: DiagramNode; weight: number }>();
  let hiddenEdges = 0;
  for (const e of edges) {
    const from = byPath.get(e.from_path);
    const to = byPath.get(e.to_path);
    if (!from || !to) {
      hiddenEdges += 1;
      continue;
    }
    // A module that calls itself is real in the data and meaningless as a
    // picture; §18.2 already excludes same-file edges, so this is belt and
    // braces against a rollup that ever stops doing so.
    if (from.id === to.id) continue;
    const key = `${from.id}>${to.id}`;
    const seen = pairs.get(key);
    if (seen) seen.weight += e.weight;
    else pairs.set(key, { from, to, weight: e.weight });
  }

  const ranked_edges = [...pairs.values()].sort(
    (a, b) =>
      b.weight - a.weight ||
      a.from.path.localeCompare(b.from.path) ||
      a.to.path.localeCompare(b.to.path),
  );
  const drawn = ranked_edges.slice(0, Math.max(0, maxEdges));
  hiddenEdges += ranked_edges.length - drawn.length;

  const lines = ["graph LR"];
  for (const n of kept) {
    const node = byPath.get(n.path)!;
    lines.push(`  ${node.id}[${quote(node.label)}]`);
  }
  const labelled = drawn.length <= EDGE_LABEL_LIMIT;
  for (const p of drawn) {
    lines.push(
      labelled
        ? `  ${p.from.id} -->|${p.weight}| ${p.to.id}`
        : `  ${p.from.id} --> ${p.to.id}`,
    );
  }

  return {
    source: lines.join("\n"),
    byId,
    omitted: Math.max(0, nodes.length - kept.length),
    hiddenEdges,
    edgesDrawn: drawn.length,
  };
}

/**
 * Recover the diagram node id from a rendered SVG element's id.
 *
 * Mermaid names node elements `flowchart-m3-7` — our id with a prefix and a
 * render-scoped suffix. Reading it back is how click-through works without
 * `securityLevel: "loose"`, which would let diagram text execute as script for
 * no benefit here.
 */
export function nodeIdFromElement(elementId: string): string | null {
  const m = /(?:^|-)(m\d+)(?:-|$)/.exec(elementId);
  return m ? m[1] : null;
}
