/**
 * Component-level coverage for ResultsGrid.tsx: it now fetches its own data
 * from GET /profiles (no more rows/onUpdateResult prop-drilling from App),
 * and PATCHes through api.patchProfile with an optimistic update + rollback
 * on failure. The filter/sort/label logic itself is covered directly in
 * resultsFilter.test.ts without rendering anything.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PlatformHealth, Profile } from "../api/types";
import { ResultsGrid } from "./ResultsGrid";

vi.mock("../api/profilesApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/profilesApi")>();
  return {
    ...actual,
    profilesApi: {
      ...actual.profilesApi,
      profiles: vi.fn(),
      patchProfile: vi.fn(),
    },
  };
});

import { profilesApi as api } from "../api/profilesApi";

const PLATFORMS: PlatformHealth[] = [
  {
    platform: "youtube",
    name: "YouTube",
    enabled: true,
    session_state: "ready",
    state: "healthy",
    score: 1,
    ok: 1,
    partial: 0,
    bad: 0,
    total: 1,
    last_error: "",
    last_seen: 0,
  },
];

function makeProfile(overrides: Partial<Profile> = {}): Profile {
  return {
    id: "row-1",
    platform: "youtube",
    url: "https://youtube.com/channel/abc",
    status: "pending",
    has_logo: true,
    phase: "discovery",
    profile_name: "Test Channel",
    risk_score: 5,
    priority: "Medium",
    followers: 100,
    ...overrides,
  };
}

function baseProps(overrides: Partial<ComponentProps<typeof ResultsGrid>> = {}) {
  return {
    clientId: "cyfirma",
    platforms: PLATFORMS,
    discoveryRunning: false,
    discoveryLog: [],
    discoveryProgress: {},
    analysisRunning: false,
    analysisLog: [],
    analysisProgress: {},
    onError: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(api.profiles).mockReset();
  vi.mocked(api.patchProfile).mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("fetching", () => {
  it("loads profiles for the given client and renders them", async () => {
    vi.mocked(api.profiles).mockResolvedValue({ items: [makeProfile()], total: 1 });
    render(<ResultsGrid {...baseProps()} />);

    await waitFor(() => expect(api.profiles).toHaveBeenCalledWith(
      expect.objectContaining({ client_id: "cyfirma", phase: "discovery" }),
    ));
    expect(await screen.findByText("Test Channel")).toBeInTheDocument();
  });

  it("shows an empty state instead of a table when nothing matches", async () => {
    vi.mocked(api.profiles).mockResolvedValue({ items: [], total: 0 });
    render(<ResultsGrid {...baseProps()} />);

    expect(await screen.findByText(/No profiles match/i)).toBeInTheDocument();
  });

  it("renders a placeholder instead of fetching when no client is set", () => {
    render(<ResultsGrid {...baseProps({ clientId: "" })} />);
    expect(api.profiles).not.toHaveBeenCalled();
    expect(screen.getByText(/Set a client/i)).toBeInTheDocument();
  });
});

describe("card view", () => {
  it("renders a profile-card per row, with a Validate action, when Cards is selected", async () => {
    const user = userEvent.setup();
    vi.mocked(api.profiles).mockResolvedValue({ items: [makeProfile()], total: 1 });
    render(<ResultsGrid {...baseProps()} />);

    await screen.findByText("Test Channel");
    await user.click(screen.getByTitle("Card view"));

    const card = document.querySelector(".profile-card");
    expect(card).toBeTruthy();
    expect(card).toHaveTextContent("Test Channel");
    expect(card).toHaveTextContent("pending");
    // no plain <table> left in the DOM once the card view is active
    expect(document.querySelector("table")).toBeNull();
  });
});

describe("validate / reject -- optimistic update and rollback", () => {
  it("updates status immediately and PATCHes the profile", async () => {
    const user = userEvent.setup();
    vi.mocked(api.profiles).mockResolvedValue({
      items: [makeProfile({ status: "pending" })],
      total: 1,
    });
    let resolvePatch!: () => void;
    vi.mocked(api.patchProfile).mockReturnValue(
      new Promise((resolve) => {
        resolvePatch = () => resolve(makeProfile({ status: "approved" }));
      }),
    );
    render(<ResultsGrid {...baseProps()} />);

    await screen.findByText("Test Channel");
    await user.click(screen.getByTitle("Table view"));
    const row = (await screen.findByText("Test Channel")).closest("tr")!;
    // scoped to the row -- the status filter chips above the table also
    // render a "✅ Validated" button and would otherwise match /Validate/ too
    await user.click(within(row).getByRole("button", { name: /Validate/ }));

    // optimistic: the row's own status cell already reads "approved" before
    // the PATCH resolves -- scoped to the row, since the status filter chips
    // above the table render the same word regardless of any row's state
    expect(await within(row).findByText("approved")).toBeInTheDocument();
    resolvePatch();
    await waitFor(() =>
      expect(api.patchProfile).toHaveBeenCalledWith("row-1", { status: "approved" }),
    );
  });

  it("rolls back to the previous status when the PATCH fails", async () => {
    const user = userEvent.setup();
    vi.mocked(api.profiles).mockResolvedValue({
      items: [makeProfile({ status: "pending" })],
      total: 1,
    });
    vi.mocked(api.patchProfile).mockRejectedValue(new Error("network down"));
    const onError = vi.fn();
    render(<ResultsGrid {...baseProps({ onError })} />);

    await screen.findByText("Test Channel");
    await user.click(screen.getByTitle("Table view"));
    const row = (await screen.findByText("Test Channel")).closest("tr")!;
    // scoped to the row -- the status filter chips above the table also
    // render a "✅ Validated" button and would otherwise match /Validate/ too
    await user.click(within(row).getByRole("button", { name: /Validate/ }));

    await waitFor(() => expect(within(row).getByText("pending")).toBeInTheDocument());
    expect(onError).toHaveBeenCalledWith("network down");
  });
});

describe("phase toggle", () => {
  it("re-queries GET /profiles with phase=analysis when the Analysis tab is clicked", async () => {
    const user = userEvent.setup();
    vi.mocked(api.profiles).mockResolvedValue({ items: [], total: 0 });
    render(<ResultsGrid {...baseProps()} />);

    await waitFor(() =>
      expect(api.profiles).toHaveBeenCalledWith(expect.objectContaining({ phase: "discovery" })),
    );

    await user.click(screen.getByText("Analysis"));

    await waitFor(() =>
      expect(api.profiles).toHaveBeenLastCalledWith(
        expect.objectContaining({ phase: "analysis" }),
      ),
    );
  });

  it("shows the Risk column only in the analysis view", async () => {
    const user = userEvent.setup();
    vi.mocked(api.profiles).mockResolvedValue({
      items: [makeProfile({ phase: "analysis", username: "testchannel" })],
      total: 1,
    });
    render(<ResultsGrid {...baseProps()} />);
    await user.click(screen.getByText("Analysis"));
    await user.click(screen.getByTitle("Table view"));

    expect(await screen.findByText("Risk")).toBeInTheDocument();
  });
});
