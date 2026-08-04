import { Skeleton } from "@/components/ui/skeleton";

/**
 * Route-level pending state for `/repos/[id]`.
 *
 * Next wraps the segment in a Suspense boundary and renders this while the
 * page resolves. Shaped like `RepoStatusView`'s own header and stat strip so
 * the transition is one step, not a jump from a generic block to the real
 * layout.
 */
export default function Loading() {
  return (
    <main className="page-container py-8" aria-busy>
      <Skeleton className="h-4 w-24" />
      <Skeleton className="mt-4 h-8 w-72" />
      <Skeleton className="mt-2 h-4 w-48" />
      <div className="mt-8 grid gap-3 sm:grid-cols-3">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
      <Skeleton className="mt-6 h-64 w-full" />
    </main>
  );
}
