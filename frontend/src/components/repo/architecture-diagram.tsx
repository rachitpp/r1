"use client";

/**
 * The §18.2 rollup drawn as a graph (FEATURE-IDEAS 3.3).
 *
 * Same data as the list beside it, different question. The list answers "what
 * does this module touch"; the picture answers "what shape is this codebase" —
 * one hub with a long tail looks nothing like a flat mesh, and that difference
 * is invisible in a ranked list no matter how carefully it is read.
 *
 * Two things are load-bearing here:
 *
 * *Mermaid is imported dynamically.* It is ~500 KB, larger than the rest of
 * this page put together, and a reader who never opens the diagram should not
 * pay for it. The import happens on first render of this component, which only
 * mounts once the tab is chosen.
 *
 * *Click-through reads the node id back out of the SVG* rather than using
 * mermaid's `click` directive, which needs `securityLevel: "loose"` — that
 * makes diagram text executable, and the diagram text here is built from repo
 * file paths. Not a trade worth making for a navigation affordance.
 */

import { Loader2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useTheme } from "@/hooks/use-theme";
import type { ModuleEdge, ModuleNode } from "@/lib/api-types";
import {
  DIAGRAM_MAX_NODES,
  buildDiagram,
  nodeIdFromElement,
} from "@/lib/mermaid-graph";

export function ArchitectureDiagram({
  nodes,
  edges,
  onPick,
}: {
  nodes: ModuleNode[];
  edges: ModuleEdge[];
  /** Clicking a box opens that module in the list beside the diagram. */
  onPick: (path: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const { resolved, ready } = useTheme();
  const [error, setError] = useState(false);
  const [rendered, setRendered] = useState(false);

  // `onPick` in a ref so a new callback identity from the parent does not
  // re-run the whole render — mermaid re-layout is expensive and visible.
  const pick = useRef(onPick);
  pick.current = onPick;

  // Memoised so the render effect below can depend on the whole object: a
  // fresh `byId` Map every render would re-run mermaid layout on every render.
  const diagram = useMemo(
    () => buildDiagram(nodes, edges, DIAGRAM_MAX_NODES),
    [nodes, edges],
  );

  useEffect(() => {
    // Wait for the resolved theme: rendering against the server's "light"
    // default and re-rendering a beat later is a visible flash on a dark page.
    if (!ready) return;
    let cancelled = false;

    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: resolved === "dark" ? "dark" : "default",
          // `linear` over the prettier `basis`: curved edges at this density
          // cross each other in ways that make two arrows indistinguishable,
          // and following one line to its head is the whole job here.
          flowchart: { curve: "linear", nodeSpacing: 26, rankSpacing: 70 },
          // The page's own typeface, so the diagram does not read as a
          // screenshot pasted in from somewhere else.
          fontFamily: "var(--font-sans), ui-sans-serif, system-ui",
        });
        // Unique per render: mermaid caches by id, and a stale entry renders
        // the previous theme's colours.
        const id = `arch-${Date.now().toString(36)}`;
        const { svg } = await mermaid.render(id, diagram.source);
        if (cancelled || !host.current) return;
        host.current.innerHTML = svg;

        // Let the SVG size itself to the container instead of the fixed width
        // mermaid bakes in, so narrow screens scroll rather than clip.
        const el = host.current.querySelector("svg");
        if (el) {
          el.removeAttribute("width");
          el.style.maxWidth = "100%";
          el.style.height = "auto";
        }

        for (const node of host.current.querySelectorAll<SVGGElement>("g.node")) {
          const nodeId = nodeIdFromElement(node.id);
          const target = nodeId ? diagram.byId.get(nodeId) : undefined;
          if (!target) continue;
          node.style.cursor = "pointer";
          const title = document.createElementNS(
            "http://www.w3.org/2000/svg",
            "title",
          );
          title.textContent = `${target.path} — ${target.fanIn} incoming`;
          node.appendChild(title);
          node.addEventListener("click", () => pick.current(target.path));
        }
        setRendered(true);
      } catch {
        // A diagram that will not draw must not take the panel with it; the
        // ranked list above is the same information and always works.
        if (!cancelled) setError(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [diagram, resolved, ready]);

  if (error) {
    return (
      <p className="py-6 text-center text-xs text-muted-foreground">
        The diagram could not be drawn. The ranked list has the same data.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-md border bg-background p-3">
        {!rendered && (
          <div className="flex items-center justify-center gap-2 py-10 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            Drawing the module graph…
          </div>
        )}
        <div ref={host} className={rendered ? "" : "hidden"} />
      </div>
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        Top {diagram.byId.size} module{diagram.byId.size === 1 ? "" : "s"} by
        fan-in and their {diagram.edgesDrawn} strongest dependencies
        {diagram.omitted > 0 && `, ${diagram.omitted} more modules in the list view`}
        . Arrows point from a module to what it depends on
        {diagram.hiddenEdges > 0 &&
          `; ${diagram.hiddenEdges} lighter edges are hidden to keep the shape readable`}
        . Click a box to open it.
      </p>
    </div>
  );
}
