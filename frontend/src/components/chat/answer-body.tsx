"use client";

/**
 * Renders a parsed agent answer: markdown blocks with `[path:start-end]`
 * citation markers turned into clickable chips in place.
 */

import { CitationChip } from "@/components/chat/citation-chip";
import { type Citation, citationKey } from "@/lib/citations";
import { type Block, type Inline } from "@/lib/markdown";

function InlineNodes({
  nodes,
  onCiteClick,
  activeKey,
}: {
  nodes: Inline[];
  onCiteClick: (c: Citation) => void;
  activeKey: string | null;
}) {
  return (
    <>
      {nodes.map((node, i) => {
        switch (node.kind) {
          case "text":
            return <span key={i}>{node.text}</span>;
          case "code":
            return (
              <code
                key={i}
                className="rounded border bg-muted px-1 py-px font-mono text-[0.85em]"
              >
                {node.text}
              </code>
            );
          case "strong":
            return (
              <strong key={i} className="font-semibold">
                <InlineNodes
                  nodes={node.children}
                  onCiteClick={onCiteClick}
                  activeKey={activeKey}
                />
              </strong>
            );
          case "em":
            return (
              <em key={i}>
                <InlineNodes
                  nodes={node.children}
                  onCiteClick={onCiteClick}
                  activeKey={activeKey}
                />
              </em>
            );
          case "citation":
            return (
              <CitationChip
                key={i}
                citation={node.citation}
                onClick={onCiteClick}
                active={activeKey === citationKey(node.citation)}
              />
            );
        }
      })}
    </>
  );
}

export function AnswerBody({
  blocks,
  onCiteClick,
  activeKey,
}: {
  blocks: Block[];
  onCiteClick: (c: Citation) => void;
  activeKey: string | null;
}) {
  return (
    <div className="max-w-[70ch] space-y-3 text-sm leading-relaxed">
      {blocks.map((block, i) => {
        switch (block.kind) {
          case "paragraph":
            return (
              <p key={i} className="whitespace-pre-wrap">
                <InlineNodes
                  nodes={block.children}
                  onCiteClick={onCiteClick}
                  activeKey={activeKey}
                />
              </p>
            );
          case "heading":
            return (
              <p
                key={i}
                className={
                  block.level <= 2
                    ? "pt-1 text-base font-semibold tracking-tight"
                    : "pt-1 text-sm font-semibold"
                }
              >
                <InlineNodes
                  nodes={block.children}
                  onCiteClick={onCiteClick}
                  activeKey={activeKey}
                />
              </p>
            );
          case "list": {
            const Tag = block.ordered ? "ol" : "ul";
            return (
              <Tag
                key={i}
                className={
                  "space-y-1 pl-5 " +
                  (block.ordered ? "list-decimal" : "list-disc")
                }
              >
                {block.items.map((item, j) => (
                  <li key={j} className="pl-0.5">
                    <InlineNodes
                      nodes={item}
                      onCiteClick={onCiteClick}
                      activeKey={activeKey}
                    />
                  </li>
                ))}
              </Tag>
            );
          }
          case "code":
            return (
              <pre
                key={i}
                className="overflow-x-auto rounded-lg border bg-muted/60 p-3 font-mono text-xs leading-relaxed"
              >
                <code>{block.code}</code>
              </pre>
            );
        }
      })}
    </div>
  );
}
