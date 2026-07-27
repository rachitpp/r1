import { describe, expect, it } from "vitest";

import { createSseParser } from "./sse";

describe("createSseParser", () => {
  it("parses a single complete event", () => {
    const p = createSseParser();
    expect(p.feed('event: status\ndata: {"state": "thinking"}\n\n')).toEqual([
      { event: "status", data: '{"state": "thinking"}' },
    ]);
  });

  it("parses several events in one chunk, in order", () => {
    const p = createSseParser();
    const events = p.feed(
      "event: text\ndata: {}\n\nevent: done\ndata: {}\n\n",
    );
    expect(events.map((e) => e.event)).toEqual(["text", "done"]);
  });

  it("buffers events split across arbitrary chunk boundaries", () => {
    // sse-starlette gives no alignment guarantees; a token delta can split
    // mid-line, mid-utf8, anywhere. Feed one byte at a time as the worst case.
    const wire = 'event: text\ndata: {"delta": "Auth starts in "}\n\n';
    const p = createSseParser();
    const events = wire.split("").flatMap((ch) => p.feed(ch));
    expect(events).toEqual([
      { event: "text", data: '{"delta": "Auth starts in "}' },
    ]);
  });

  it("joins multi-line data with newlines", () => {
    const p = createSseParser();
    expect(p.feed("data: line1\ndata: line2\n\n")).toEqual([
      { event: "message", data: "line1\nline2" },
    ]);
  });

  it("ignores comment lines (keep-alives) and unknown fields", () => {
    const p = createSseParser();
    const events = p.feed(
      ": ping\nid: 7\nretry: 1000\nevent: done\ndata: {}\n\n",
    );
    expect(events).toEqual([{ event: "done", data: "{}" }]);
  });

  it("handles CRLF line endings", () => {
    const p = createSseParser();
    expect(p.feed("event: done\r\ndata: {}\r\n\r\n")).toEqual([
      { event: "done", data: "{}" },
    ]);
  });

  it("never dispatches a trailing partial event", () => {
    const p = createSseParser();
    expect(p.feed("event: text\ndata: {\"delta\"")).toEqual([]);
  });

  it("a blank line without pending data dispatches nothing", () => {
    const p = createSseParser();
    expect(p.feed("\n\n\n")).toEqual([]);
  });

  it("survives the recorded §9 sequence shape end-to-end", () => {
    const wire = [
      'event: status\ndata: {"state": "thinking"}\n\n',
      'event: tool_call\ndata: {"n": 1, "tool": "search_code", "args": {"query": "q"}}\n\n',
      'event: tool_result\ndata: {"n": 1, "tool": "search_code", "summary": "10 hits", "locations": []}\n\n',
      'event: text\ndata: {"delta": "The"}\n\n',
      'event: text\ndata: {"delta": " answer"}\n\n',
      'event: citations\ndata: {"citations": []}\n\n',
      'event: done\ndata: {"tool_calls_used": 1}\n\n',
    ].join("");
    const p = createSseParser();
    const names = p.feed(wire).map((e) => e.event);
    expect(names).toEqual([
      "status",
      "tool_call",
      "tool_result",
      "text",
      "text",
      "citations",
      "done",
    ]);
  });
});
