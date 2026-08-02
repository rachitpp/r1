import type { Metadata } from "next";

import { SharedAnswerView } from "@/components/shared-answer-view";

/**
 * A published answer, at a permanent URL (SPEC §21).
 *
 * **The only route in this app that does not assume a session.** Everything
 * else is `/repos/...` behind §13 ownership; this is what someone opens from a
 * link in a PR or a Slack message, usually without an account here at all. So
 * it renders from `GET /shared/{id}` alone and never touches an owned route —
 * a citation links to GitHub at the pinned commit rather than to the code
 * viewer, which the reader could not open anyway.
 */
export const metadata: Metadata = {
  title: "Shared answer",
  // A permalink is meant to be pasted around; there is no reason for search
  // engines to index one, and the id is a capability rather than a public name.
  robots: { index: false, follow: false },
};

export default async function SharedAnswerPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <SharedAnswerView shareId={id} />;
}
