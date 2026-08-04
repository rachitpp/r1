import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RequireAuth } from "@/components/auth/require-auth";
import type { UseUser } from "@/hooks/use-user";
import { ApiError } from "@/lib/api-client";

/**
 * The gate's whole job is telling four situations apart, so the session hook is
 * stubbed rather than driven through TanStack — the branch under test is the
 * rendering decision, not the fetch.
 */
const state = vi.hoisted(() => ({ current: null as unknown as UseUser }));

vi.mock("@/hooks/use-user", () => ({
  useUser: () => state.current,
  useLogout: () => () => {},
}));

const SIGNED_OUT: UseUser = {
  user: null,
  isLoading: false,
  isUnreachable: false,
  error: null,
};

const child = <p>secret repo</p>;

describe("RequireAuth", () => {
  beforeEach(() => {
    state.current = SIGNED_OUT;
  });

  it("renders children once there is a user", () => {
    state.current = {
      ...SIGNED_OUT,
      user: {
        id: "u1",
        login: "octocat",
        name: null,
        avatar_url: null,
        created_at: "2026-01-01T00:00:00Z",
      },
    };
    render(<RequireAuth>{child}</RequireAuth>);
    expect(screen.getByText("secret repo")).toBeTruthy();
  });

  it("withholds children while the session check is in flight", () => {
    state.current = { ...SIGNED_OUT, isLoading: true };
    render(<RequireAuth>{child}</RequireAuth>);
    expect(screen.queryByText("secret repo")).toBeNull();
  });

  it("does not offer sign-in when the API is simply unreachable", () => {
    // Offering "Sign in with GitHub" against a dead backend sends the user
    // through a redirect chain that cannot succeed.
    state.current = { ...SIGNED_OUT, isUnreachable: true };
    render(<RequireAuth>{child}</RequireAuth>);

    expect(screen.getByText(/Can.t reach the API/)).toBeTruthy();
    expect(screen.queryByText(/Sign in with GitHub/)).toBeNull();
    expect(screen.queryByText("secret repo")).toBeNull();
  });

  it("quotes the request id when the session check errors", () => {
    state.current = {
      ...SIGNED_OUT,
      error: new ApiError(500, "internal server error", "req-abc123"),
    };
    render(<RequireAuth>{child}</RequireAuth>);

    expect(screen.getByText(/Couldn.t verify your session/)).toBeTruthy();
    expect(screen.getByText("(req-abc123)")).toBeTruthy();
  });

  it("shows the caller's own copy on the sign-in card", () => {
    render(
      <RequireAuth title="Sign in to ask questions" description="Repos are private.">
        {child}
      </RequireAuth>,
    );
    expect(screen.getByText("Sign in to ask questions")).toBeTruthy();
    expect(screen.getByText("Repos are private.")).toBeTruthy();
    expect(screen.queryByText("secret repo")).toBeNull();
  });
});
