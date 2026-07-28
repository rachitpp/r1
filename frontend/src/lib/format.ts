/**
 * Display helpers for repo rows. Everything here is derived from the §8
 * `RepoOut` shape — no new API surface.
 */

/** `encode/httpx` → `{ owner: "encode", name: "httpx" }`; falls back whole. */
export function splitRepoName(fullName: string): {
  owner: string | null;
  name: string;
} {
  const slash = fullName.indexOf("/");
  if (slash < 0) return { owner: null, name: fullName };
  return {
    owner: fullName.slice(0, slash),
    name: fullName.slice(slash + 1),
  };
}

/**
 * The ingest strategy tag the backend hangs off the URL fragment
 * (`…/httpx#naive`), surfaced so a benchmark row does not read as a typo in
 * the repo name. Returns null for a normal repo.
 */
export function strategyTag(url: string): string | null {
  const hash = url.indexOf("#");
  if (hash < 0) return null;
  const tag = url.slice(hash + 1).trim();
  return tag || null;
}

/** `encode/httpx@naive` → `encode/httpx`; the tag renders as its own badge. */
export function stripStrategySuffix(fullName: string): string {
  const at = fullName.lastIndexOf("@");
  return at > 0 ? fullName.slice(0, at) : fullName;
}

/**
 * A GitHub blob permalink for a cited range, or null when the pieces are
 * missing. The strategy fragment (`…/httpx#naive`) is an ingest marker, not part
 * of the repo URL, so it is stripped before building the link.
 */
export function githubBlobUrl(
  repoUrl: string | undefined,
  headSha: string | null | undefined,
  citation: { file_path: string; start_line: number; end_line: number },
): string | null {
  if (!repoUrl || !headSha) return null;
  const base = repoUrl.split("#")[0].replace(/\.git$/, "").replace(/\/$/, "");
  if (!base.startsWith("https://github.com/")) return null;
  return `${base}/blob/${headSha}/${citation.file_path}#L${citation.start_line}-L${citation.end_line}`;
}

/** First 7 of a commit sha, the length git itself abbreviates to. */
export function shortSha(sha: string | null): string | null {
  return sha ? sha.slice(0, 7) : null;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * Coarse relative time — "4h ago". Rendered client-side only (the repo list is
 * a client query), so there is no server/client clock mismatch to hydrate.
 */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const delta = Math.max(0, now - then);
  if (delta < MINUTE) return "just now";
  if (delta < HOUR) return `${Math.floor(delta / MINUTE)}m ago`;
  if (delta < DAY) return `${Math.floor(delta / HOUR)}h ago`;
  if (delta < 7 * DAY) return `${Math.floor(delta / DAY)}d ago`;
  return `${Math.floor(delta / (7 * DAY))}w ago`;
}
