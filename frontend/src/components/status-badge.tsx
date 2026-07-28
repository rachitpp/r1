import { Badge } from "@/components/ui/badge";
import type { RepoStatus } from "@/lib/api";
import { IN_FLIGHT_STATUSES } from "@/lib/api";
import { cn } from "@/lib/utils";

/** One place that knows what each §10 state should look like. */
const STYLES: Record<RepoStatus, string> = {
  queued: "bg-muted text-muted-foreground",
  cloning: "bg-blue-100 text-blue-900",
  parsing: "bg-blue-100 text-blue-900",
  linking: "bg-indigo-100 text-indigo-900",
  embedding: "bg-violet-100 text-violet-900",
  ready: "bg-emerald-100 text-emerald-900",
  failed: "bg-red-100 text-red-900",
};

export function StatusBadge({ status }: { status: RepoStatus }) {
  const inFlight = IN_FLIGHT_STATUSES.includes(status);
  return (
    <Badge
      variant="outline"
      className={cn(
        "shrink-0 gap-1.5 border-transparent font-medium",
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
