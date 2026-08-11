import { useNavigate } from "react-router-dom"
import { SectionHeader } from "@/components/layout/sidebar/SectionHeader"
import { ConversationItem } from "@/components/layout/sidebar/ConversationItem"
import type { components } from "@/api/schema"

type ConversationResponse = components["schemas"]["ConversationResponse"]
type GroupResponse = components["schemas"]["GroupResponse"]

interface UngroupedSectionProps {
  conversations: ConversationResponse[]
  groups: GroupResponse[]
  activeConversationId: string | undefined
  isCollapsed: boolean
  onToggle: () => void
  onNavigate?: () => void
}

// The Ungrouped section always exists (it's where every chat starts and where
// a deleted group's chats land) — unlike a real group it has no rename/delete,
// so it doesn't need GroupSection's mutation/dialog machinery at all.
export function UngroupedSection({
  conversations,
  groups,
  activeConversationId,
  isCollapsed,
  onToggle,
  onNavigate,
}: UngroupedSectionProps) {
  const navigate = useNavigate()

  function handleNewChat() {
    navigate("/chat", { state: { newChatNonce: crypto.randomUUID() } })
    onNavigate?.()
  }

  return (
    <div className="mt-2">
      <SectionHeader
        title="Ungrouped"
        isCollapsed={isCollapsed}
        onToggle={onToggle}
        onNewChat={handleNewChat}
      />
      {!isCollapsed && (
        <div className="mt-0.5 flex flex-col gap-1">
          {conversations.length === 0 && (
            <p className="px-4 py-1 text-xs text-sidebar-foreground/50">No chats here yet.</p>
          )}
          {conversations.map((conversation) => (
            <ConversationItem
              key={conversation.id}
              id={conversation.id}
              title={conversation.title}
              groupId={conversation.group_id}
              groups={groups}
              isActive={conversation.id === activeConversationId}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      )}
    </div>
  )
}
