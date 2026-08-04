import { Badge } from "@/components/ui/badge";
import { IN_FLIGHT_STATUSES, type RepoStatus } from "@/lib/api-types";
import { cn } from "@/lib/utils";

/**
 * One place that knows what each §10 state should look like.
 *
 * Three inks, not seven. The four in-flight states are all the same ochre —
 * the distinction between "cloning" and "linking" is carried by the label and
 * the pulse, not by a fifth hue. Giving each state its own Tailwind colour was
 * what made this read as a palette demo.
 */
const STYLES: Record<RepoStatus, string> = {
  queued: "bg-muted text-muted-foreground",
  cloning: "bg-[hsl(var(--ochre)/0.13)] text-[hsl(var(--ochre))]",
  parsing: "bg-[hsl(var(--ochre)/0.13)] text-[hsl(var(--ochre))]",
  linking: "bg-[hsl(var(--ochre)/0.13)] text-[hsl(var(--ochre))]",
  embedding: "bg-[hsl(var(--ochre)/0.13)] text-[hsl(var(--ochre))]",
  ready: "bg-[hsl(var(--sage)/0.14)] text-[hsl(var(--sage))]",
  failed: "bg-destructive/12 text-destructive",
};

export function StatusBadge({ status }: { status: RepoStatus }) {
  const inFlight = IN_FLIGHT_STATUSES.includes(status);
  return (
    <Badge
      variant="outline"
      className={cn(
        "shrink-0 gap-1.5 rounded-sm border-transparent font-mono text-[11px] font-medium lowercase tracking-wide",
        STYLES[status],
      )}
    >
      {inFlight && (
        <span
          aria-hidden
          className="size-1.5 animate-pulse rounded-full bg-current"
        />
      )}
      {status}
    </Badge>
  );
}
