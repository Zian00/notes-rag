import { Navigate, Route, Routes } from "react-router-dom"
import { ProtectedRoute } from "@/routes/ProtectedRoute"
import { LoginPage } from "@/routes/LoginPage"
import { RegisterPage } from "@/routes/RegisterPage"
import { ChatPage } from "@/routes/ChatPage"
import { DocumentsPage } from "@/routes/DocumentsPage"
import { AppShell } from "@/components/layout/AppShell"

// Mountable under any router (BrowserRouter in App.tsx, MemoryRouter in tests)
// so the route tree itself stays test-friendly without duplicating it.
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />

      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/chat/:conversationId" element={<ChatPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
        </Route>
      </Route>

      <Route path="/" element={<Navigate to="/chat" replace />} />
      {/* Unknown paths fall through to "/", which redirects to "/chat", which
          ProtectedRoute guards to "/login" for anon users — no blank pages. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
