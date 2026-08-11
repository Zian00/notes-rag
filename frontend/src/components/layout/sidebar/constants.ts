// Sentinel id for the always-present "Ungrouped" section, distinct from any
// real group's UUID — used as a collapse-state key and a droppable id, never
// sent to the backend (ungrouped chats are identified by group_id: null).
// Lives in its own module so both Sidebar and UngroupedSection can share it
// without a circular import (Sidebar renders UngroupedSection).
export const UNGROUPED_SECTION_ID = "__ungrouped__"
