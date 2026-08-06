import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import type { ReactElement } from 'react'

import { AuthProvider, useAuth } from '@/auth/AuthContext'
import { RequireRole, homePathFor } from '@/auth/RequireRole'
import { OfflineBanner } from '@/components/layout/OfflineBanner'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { LoginPage } from '@/routes/LoginPage'
import { Portal } from '@/routes/Portal'
import { BackendModeProvider } from '@/state/backendMode'

/** Sends the root path to the signed-in user's portal, or to login. */
function RootRedirect(): ReactElement {
  const { session } = useAuth()
  return <Navigate to={session ? homePathFor(session.role) : '/login'} replace />
}

/** Route table: a login surface plus two role-gated portals. */
function AppRoutes(): ReactElement {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/admin"
        element={
          <RequireRole role="admin">
            <Portal role="admin" />
          </RequireRole>
        }
      />
      <Route
        path="/app"
        element={
          <RequireRole role="user">
            <Portal role="user" />
          </RequireRole>
        }
      />
      <Route path="/" element={<RootRedirect />} />
      <Route path="*" element={<RootRedirect />} />
    </Routes>
  )
}

/**
 * Application root: providers + router. The {@link BackendModeProvider} runs the
 * live-first boot probe and drives the {@link OfflineBanner}, which sits above
 * every route so a mock/fallback session is always clearly labelled.
 */
export function App(): ReactElement {
  return (
    <BackendModeProvider>
      <AuthProvider>
        <TooltipProvider>
          <BrowserRouter>
            <div className="flex h-dvh flex-col overflow-hidden">
              <OfflineBanner />
              <div className="min-h-0 flex-1">
                <AppRoutes />
              </div>
            </div>
          </BrowserRouter>
          <Toaster />
        </TooltipProvider>
      </AuthProvider>
    </BackendModeProvider>
  )
}

export default App
