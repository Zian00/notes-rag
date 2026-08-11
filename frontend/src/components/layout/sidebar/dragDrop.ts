import { UNGROUPED_SECTION_ID } from "@/components/layout/sidebar/constants"

// Maps a section's droppable id (a real group's id, or the Ungrouped
// sentinel) to the `group_id` value the PATCH body needs — the inverse of
// how ConversationResponse.group_id already represents "no group" as null.
function groupIdForSection(sectionId: string): string | null {
  return sectionId === UNGROUPED_SECTION_ID ? null : sectionId
}

// Decides what a drop should do, given the chat's current group and the
// section it was dropped on. Returns the group_id to send, or null if the
// drop is a no-op (dropped onto the section the chat is already in) — kept
// pure and separate from Sidebar's onDragEnd so this decision is unit-testable
// without simulating a real pointer/keyboard drag gesture in jsdom.
export function resolveDropTarget(
  currentGroupId: string | null,
  droppedSectionId: string
): { groupId: string | null } | null {
  const targetGroupId = groupIdForSection(droppedSectionId)
  if (targetGroupId === currentGroupId) return null
  return { groupId: targetGroupId }
}
