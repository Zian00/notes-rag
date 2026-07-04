import { QueryClient } from "@tanstack/react-query"

// Centralized React Query defaults for the app.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 4xx errors (auth failures, validation errors) are deterministic —
      // retrying them just wastes a round trip, so only network/5xx-style
      // transient errors would benefit from retries, which we're opting out
      // of entirely for simplicity here.
      retry: false,
      // Treat fetched data as fresh for 30s so navigating between views
      // (e.g. documents list <-> chat) doesn't refetch on every mount.
      staleTime: 30_000,
    },
  },
})
