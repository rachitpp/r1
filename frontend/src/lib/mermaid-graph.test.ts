import { describe, expect, it } from "vitest";

import type { ModuleEdge, ModuleNode } from "@/lib/api-types";
import {
  buildDiagram,
  nodeIdFromElement,
} from "@/lib/mermaid-graph";

const NODES: ModuleNode[] = [
  { path: "pkg/core.py", n_symbols: 10, fan_in: 9, fan_out: 1 },
  { path: "pkg/util.py", n_symbols: 4, fan_in: 3, fan_out: 0 },
  { path: "pkg/api.py", n_symbols: 6, fan_in: 0, fan_out: 5 },
];

const EDGES: ModuleEdge[] = [
  { from_path: "pkg/api.py", to_path: "pkg/core.py", kind: "calls", weight: 4 },
  { from_path: "pkg/api.py", to_path: "pkg/core.py", kind: "imports", weight: 1 },
  { from_path: "pkg/api.py", to_path: "pkg/util.py", kind: "imports", weight: 2 },
];

describe("buildDiagram", () => {
  it("emits a left-right graph with one node line per module", () => {
    const { source } = buildDiagram(NODES, EDGES);
    expect(source.startsWith("graph LR")).toBe(true);
    expect(source).toContain('m0["core.py"]');
    expect(source).toContain('m1["util.py"]');
    expect(source).toContain('m2["api.py"]');
  });

  it("ranks nodes by fan-in so ids match the panel's ordering", () => {
    const { byId } = buildDiagram(NODES, EDGES);
    expect(byId.get("m0")?.path).toBe("pkg/core.py");
    expect(byId.get("m2")?.path).toBe("pkg/api.py");
  });

  it("merges edge kinds into one arrow carrying the summed weight", () => {
    const { source } = buildDiagram(NODES, EDGES);
    // calls(4) + imports(1) is one arrow of 5, not two parallel arrows.
    expect(source).toContain("m2 -->|5| m0");
    expect(source).not.toContain("-->|4|");
    expect(source).toContain("m2 -->|2| m1");
  });

  it("labels with the filename, keeping the full path for click-through", () => {
    const { byId } = buildDiagram(NODES, EDGES);
    expect(byId.get("m0")).toMatchObject({
      label: "core.py",
      path: "pkg/core.py",
    });
  });

  it("caps at the top N by fan-in and reports what it dropped", () => {
    const { source, omitted } = buildDiagram(NODES, EDGES, 2);
    expect(omitted).toBe(1);
    expect(source).toContain('m0["core.py"]');
    expect(source).not.toContain("api.py");
  });

  it("drops edges whose endpoints fell outside the cap, and counts them", () => {
    // api.py is cut, so both of its edges have nowhere to land.
    const { source, hiddenEdges } = buildDiagram(NODES, EDGES, 2);
    expect(hiddenEdges).toBe(3);
    expect(source).not.toContain("-->");
  });

  it("keeps only the heaviest edges, counting the rest as hidden", () => {
    const { source, edgesDrawn, hiddenEdges } = buildDiagram(NODES, EDGES, 3, 1);
    // The 5-weight pair outranks the 2-weight one.
    expect(edgesDrawn).toBe(1);
    expect(hiddenEdges).toBe(1);
    expect(source).toContain("m2 -->|5| m0");
    // util.py stays on the diagram as a box; only its lighter arrow is cut.
    expect(source).toContain('m1["util.py"]');
    expect(source).not.toContain("--> m1");
    expect(source).not.toContain("|2|");
  });

  it("drops the weight labels once there are too many arrows to read them", () => {
    // 16 modules in a star, all pointing at the hub: past the label limit.
    const many: ModuleNode[] = [
      { path: "hub.py", n_symbols: 1, fan_in: 99, fan_out: 0 },
      ...Array.from({ length: 16 }, (_, i) => ({
        path: `leaf${i}.py`,
        n_symbols: 1,
        fan_in: 0,
        fan_out: 1,
      })),
    ];
    const spokes: ModuleEdge[] = Array.from({ length: 16 }, (_, i) => ({
      from_path: `leaf${i}.py`,
      to_path: "hub.py",
      kind: "calls",
      weight: 1,
    }));
    const { source, edgesDrawn } = buildDiagram(many, spokes, 20, 16);
    expect(edgesDrawn).toBe(16);
    expect(source).toContain("--> m0");
    expect(source).not.toContain("-->|");
  });
});

describe("node labels", () => {
  it("disambiguates two modules that share a filename", () => {
    // flask really does have both of these, and two boxes reading "app.py" is
    // not a shortening — it is a wrong label.
    const clash: ModuleNode[] = [
      { path: "src/flask/app.py", n_symbols: 9, fan_in: 5, fan_out: 2 },
      { path: "src/flask/sansio/app.py", n_symbols: 4, fan_in: 3, fan_out: 1 },
      { path: "src/flask/helpers.py", n_symbols: 6, fan_in: 2, fan_out: 0 },
    ];
    const labels = [...buildDiagram(clash, []).byId.values()].map((n) => n.label);
    expect(labels).toContain("flask/app.py");
    expect(labels).toContain("sansio/app.py");
    // The one with no collision keeps the short form.
    expect(labels).toContain("helpers.py");
  });

  it("always qualifies __init__.py, which names nothing on its own", () => {
    const pkg: ModuleNode[] = [
      { path: "src/flask/__init__.py", n_symbols: 2, fan_in: 4, fan_out: 1 },
    ];
    expect([...buildDiagram(pkg, []).byId.values()][0].label).toBe(
      "flask/__init__.py",
    );
  });

  it("escapes a quote in a path instead of ending the label early", () => {
    const odd: ModuleNode[] = [
      { path: 'weird/a".py', n_symbols: 1, fan_in: 1, fan_out: 0 },
    ];
    const { source } = buildDiagram(odd, []);
    expect(source).toContain('m0["a#quot;.py"]');
    // The label must still be a single balanced pair of quotes.
    expect(source.match(/"/g)).toHaveLength(2);
  });

  it("never draws a self-loop even if the rollup produces one", () => {
    const selfEdge: ModuleEdge[] = [
      { from_path: "pkg/core.py", to_path: "pkg/core.py", kind: "calls", weight: 3 },
    ];
    expect(buildDiagram(NODES, selfEdge).source).not.toContain("m0 -->");
  });

  it("handles a repo with no resolved cross-module edges", () => {
    const { source, hiddenEdges } = buildDiagram(NODES, []);
    expect(source).toContain('m0["core.py"]');
    expect(source).not.toContain("-->");
    expect(hiddenEdges).toBe(0);
  });

  it("survives an empty rollup rather than emitting a broken graph", () => {
    const { source, byId, omitted } = buildDiagram([], []);
    expect(source).toBe("graph LR");
    expect(byId.size).toBe(0);
    expect(omitted).toBe(0);
  });
});

describe("nodeIdFromElement", () => {
  it("recovers the id from mermaid's rendered element id", () => {
    expect(nodeIdFromElement("flowchart-m3-7")).toBe("m3");
    expect(nodeIdFromElement("flowchart-m11-0")).toBe("m11");
  });

  it("returns null for anything that is not one of our nodes", () => {
    expect(nodeIdFromElement("flowchart-pointEnd")).toBeNull();
    expect(nodeIdFromElement("")).toBeNull();
  });
});
