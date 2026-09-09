import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { api } from './api/client'
import { AdminPage } from './pages/AdminPage'
import { DashboardPage } from './pages/DashboardPage'
import { LocationsPage } from './pages/LocationsPage'
import { LoginPage } from './pages/LoginPage'
import type { User } from './types/api'

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [restoring, setRestoring] = useState(true)
  const [streamConnected, setStreamConnected] = useState(false)

  useEffect(() => {
    // A page reload should not force a re-login: the refresh token in storage
    // is exchanged for a fresh access token held only in memory.
    void api.restoreSession().then((restored) => {
      setUser(restored)
      setRestoring(false)
    })
  }, [])

  if (restoring) {
    return (
      <div className="login-page">
        <div className="login-card" style={{ textAlign: 'center' }}>
          <span className="spinner" aria-hidden="true" />
          <p className="sub" style={{ marginTop: 12, marginBottom: 0 }}>
            Restoring your session…
          </p>
        </div>
      </div>
    )
  }

  if (!user) return <LoginPage onSignedIn={setUser} />

  const can = (permission: string) => user.permissions.includes(permission)
  const isAdmin = user.role === 'ADMIN'

  return (
    <div className="app">
      <header className="topbar">
        <span className="topbar__title">CHENNAI SERVICEABILITY OPERATIONS</span>
        <nav className="topbar__nav">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/locations">Locations</NavLink>
          {can('CONFIG_READ') && <NavLink to="/admin">Administration</NavLink>}
        </nav>
        <div className="topbar__spacer" />
        <span
          className={`stream-pill${streamConnected ? '' : ' stream-pill--down'}`}
          title={
            streamConnected
              ? 'Receiving live updates from the server'
              : 'Live updates disconnected — reconnecting automatically'
          }
        >
          <span className="stream-pill__dot" aria-hidden="true" />
          {streamConnected ? 'Live' : 'Reconnecting'}
        </span>
        <div className="topbar__user">
          {user.fullName}
          <span className="topbar__role">{user.role.replace(/_/g, ' ')}</span>
        </div>
        <button
          type="button"
          className="btn btn--secondary btn--sm"
          onClick={() => void api.logout().then(() => setUser(null))}
        >
          Sign out
        </button>
      </header>

      <Routes>
        <Route
          path="/"
          element={
            <DashboardPage
              canCreate={can('CUSTOMER_CREATE')}
              canAdjustLocation={can('CUSTOMER_LOCATION_ADJUST')}
              canDeleteChecks={can('AUDIT_DELETE')}
              canManageWarehouses={can('CONFIG_WRITE_GENERAL')}
              canManageServiceLocations={can('SERVICE_LOCATION_WRITE')}
              onStreamState={setStreamConnected}
            />
          }
        />
        <Route
          path="/locations"
          element={
            <LocationsPage
              canManageWarehouses={can('CONFIG_WRITE_GENERAL')}
              canManageServiceLocations={can('SERVICE_LOCATION_WRITE')}
            />
          }
        />
        <Route
          path="/admin"
          element={
            can('CONFIG_READ') ? (
              <AdminPage
                canWriteConfig={
                  can('CONFIG_WRITE_GENERAL') || can('CONFIG_WRITE_THRESHOLD')
                }
                isAdmin={isAdmin}
              />
            ) : (
              <Navigate to="/" replace />
            )
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  )
}
