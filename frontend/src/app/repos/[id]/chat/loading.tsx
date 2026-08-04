import { Skeleton } from "@/components/ui/skeleton";

/**
 * Route-level pending state for the chat page.
 *
 * This is also the Suspense boundary `ChatView` requires: it reads `?q=` via
 * `useSearchParams`, which opts the subtree into client-side rendering and
 * fails the build without a boundary above it. A `loading.tsx` supplies one for
 * the whole segment, which is why the page no longer wraps `ChatView` itself.
 */
export default function Loading() {
  return (
    <div className="page-container space-y-4 py-10" aria-busy>
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}
