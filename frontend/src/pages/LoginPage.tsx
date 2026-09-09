import { useState } from 'react'

import { api } from '../api/client'
import type { User } from '../types/api'

export function LoginPage({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onSignedIn(await api.login(username, password))
    } catch (exception) {
      setError((exception as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <h1>Chennai Serviceability</h1>
        <p className="sub">Operations sign-in</p>

        {error && (
          <div className="alert alert--error" role="alert">
            <span aria-hidden="true">⚠</span>
            <div className="alert__body">{error}</div>
          </div>
        )}

        <div className="field">
          <label htmlFor="username">Username</label>
          <input
            id="username"
            autoComplete="username"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            disabled={busy}
          />
        </div>

        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={busy}
          />
        </div>

        <button type="submit" className="btn btn--block" disabled={busy}>
          {busy ? <span className="spinner" aria-hidden="true" /> : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
