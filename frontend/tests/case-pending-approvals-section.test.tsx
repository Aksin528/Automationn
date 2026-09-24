/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { InteractionRead } from "@/client"
import { PendingApprovalsSection } from "@/components/cases/case-pending-approvals-section"
import { useAuth } from "@/hooks/use-auth"
import type { TracecatApiError } from "@/lib/errors"
import { usePendingApprovals, useVoteOnInteraction } from "@/lib/hooks"

jest.mock("@/hooks/use-auth", () => ({
  useAuth: jest.fn(),
}))

jest.mock("@/lib/hooks", () => ({
  usePendingApprovals: jest.fn(),
  useVoteOnInteraction: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

const mockUseAuth = useAuth as jest.MockedFunction<typeof useAuth>
const mockUsePendingApprovals = usePendingApprovals as jest.MockedFunction<
  typeof usePendingApprovals
>
const mockUseVoteOnInteraction = useVoteOnInteraction as jest.MockedFunction<
  typeof useVoteOnInteraction
>

function renderSection() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <PendingApprovalsSection caseId="case-1" workspaceId="workspace-1" />
    </QueryClientProvider>
  )
}

function makeInteraction(
  overrides: Partial<InteractionRead> = {}
): InteractionRead {
  return {
    id: "interaction-1",
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    type: "approval",
    status: "pending",
    request_payload: { required_approvers: 3 },
    response_payload: null,
    expires_at: null,
    wf_exec_id: "wf_test/exec_abc123",
    actor: null,
    action_ref: "isolate_host",
    action_type: "core.transform.reshape",
    current_approvals: 1,
    ...overrides,
  }
}

describe("PendingApprovalsSection", () => {
  const voteOnInteraction = jest.fn().mockResolvedValue({
    resolution: null,
    approve_count: 2,
    required_approvers: 3,
  })

  beforeEach(() => {
    jest.clearAllMocks()
    mockUseAuth.mockReturnValue({
      user: { id: "user-current" } as ReturnType<typeof useAuth>["user"],
      userIsLoading: false,
      userError: null,
    })
    mockUseVoteOnInteraction.mockReturnValue({
      voteOnInteraction,
      voteOnInteractionIsPending: false,
      voteOnInteractionError: null,
    })
  })

  it("renders a pending approval with its action, message, and count", () => {
    mockUsePendingApprovals.mockReturnValue({
      pendingApprovals: [
        makeInteraction({
          request_payload: {
            required_approvers: 3,
            message: "Please review before isolating this host",
          },
          current_approvals: 1,
        }),
      ],
      pendingApprovalsIsLoading: false,
      pendingApprovalsError: null,
    })

    renderSection()

    expect(screen.getByText("isolate_host")).toBeInTheDocument()
    expect(
      screen.getByText("Please review before isolating this host")
    ).toBeInTheDocument()
    expect(screen.getByText("1 / 3 approvals")).toBeInTheDocument()
  })

  it("shows the empty state once nothing is pending (resolved state)", () => {
    mockUsePendingApprovals.mockReturnValue({
      pendingApprovals: [],
      pendingApprovalsIsLoading: false,
      pendingApprovalsError: null,
    })

    renderSection()

    expect(
      screen.getByText("No pending approvals for this case.")
    ).toBeInTheDocument()
    expect(screen.queryByText("isolate_host")).not.toBeInTheDocument()
  })

  it("submits an approve vote with the typed comment", async () => {
    mockUsePendingApprovals.mockReturnValue({
      pendingApprovals: [makeInteraction()],
      pendingApprovalsIsLoading: false,
      pendingApprovalsError: null,
    })

    renderSection()

    fireEvent.change(
      screen.getByPlaceholderText("Add an optional comment..."),
      { target: { value: "Looks legitimate" } }
    )
    fireEvent.click(screen.getByRole("button", { name: /approve/i }))

    await waitFor(() => {
      expect(voteOnInteraction).toHaveBeenCalledWith({
        executionId: "wf_test/exec_abc123",
        interactionId: "interaction-1",
        requestBody: {
          decision: "approve",
          comment: "Looks legitimate",
        },
      })
    })
  })

  it("submits a reject vote with no comment as null", async () => {
    mockUsePendingApprovals.mockReturnValue({
      pendingApprovals: [makeInteraction()],
      pendingApprovalsIsLoading: false,
      pendingApprovalsError: null,
    })

    renderSection()

    fireEvent.click(screen.getByRole("button", { name: /reject/i }))

    await waitFor(() => {
      expect(voteOnInteraction).toHaveBeenCalledWith({
        executionId: "wf_test/exec_abc123",
        interactionId: "interaction-1",
        requestBody: {
          decision: "reject",
          comment: null,
        },
      })
    })
  })

  it("disables Approve/Reject and explains why for a user outside the approver group", () => {
    mockUsePendingApprovals.mockReturnValue({
      pendingApprovals: [
        makeInteraction({
          request_payload: {
            required_approvers: 1,
            eligible_approver_ids: ["someone-else"],
          },
        }),
      ],
      pendingApprovalsIsLoading: false,
      pendingApprovalsError: null,
    })

    renderSection()

    expect(
      screen.getByText(
        "You're not a member of any group eligible to approve this request."
      )
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /approve/i })).toBeDisabled()
    expect(screen.getByRole("button", { name: /reject/i })).toBeDisabled()
    expect(voteOnInteraction).not.toHaveBeenCalled()
  })

  it("disables Approve/Reject for the requester under separation of duties", () => {
    mockUsePendingApprovals.mockReturnValue({
      pendingApprovals: [
        makeInteraction({
          request_payload: {
            required_approvers: 1,
            separation_of_duties: true,
            requester_id: "user-current",
          },
        }),
      ],
      pendingApprovalsIsLoading: false,
      pendingApprovalsError: null,
    })

    renderSection()

    expect(
      screen.getByText(
        "You requested this action, so you can't also approve or reject it."
      )
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /approve/i })).toBeDisabled()
  })

  it("shows a loading skeleton while fetching", () => {
    mockUsePendingApprovals.mockReturnValue({
      pendingApprovals: undefined,
      pendingApprovalsIsLoading: true,
      pendingApprovalsError: null,
    })

    const { container } = renderSection()

    expect(container.querySelector(".animate-pulse")).not.toBeNull()
  })

  it("shows an error message when the fetch fails", () => {
    mockUsePendingApprovals.mockReturnValue({
      pendingApprovals: undefined,
      pendingApprovalsIsLoading: false,
      pendingApprovalsError: new Error(
        "network error"
      ) as unknown as TracecatApiError,
    })

    renderSection()

    expect(
      screen.getByText("Failed to load pending approvals")
    ).toBeInTheDocument()
  })
})
