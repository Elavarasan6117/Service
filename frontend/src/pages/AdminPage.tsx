import { useEffect, useState } from 'react'

import { ApiError, api } from '../api/client'
import type { AppConfigItem, ConfigAuditItem } from '../types/api'

interface Props {
  canWriteConfig: boolean
  isAdmin: boolean
}

const THRESHOLD_KEY = 'SERVICEABILITY_RADIUS_METERS'

export function AdminPage({ canWriteConfig, isAdmin }: Props) {
  const [config, setConfig] = useState<AppConfigItem[]>([])
  const [history, setHistory] = useState<ConfigAuditItem[]>([])
  const [editing, setEditing] = useState<string | null>(null)
  const [draftValue, setDraftValue] = useState('')
  const [reason, setReason] = useState('')
  const [message, setMessage] = useState<{ tone: string; text: string } | null>(null)
  const [usage, setUsage] = useState<{
    provider: string
    healthy: boolean
    cache: Record<string, unknown>
  } | null>(null)

  const load = async () => {
    try {
      const [items, thresholdHistory] = await Promise.all([
        api.listConfig(),
        api.configHistory(THRESHOLD_KEY).catch(() => []),
      ])
      setConfig(items)
      setHistory(thresholdHistory)
    } catch (exception) {
      setMessage({ tone: 'error', text: (exception as Error).message })
    }
    try {
      setUsage(await api.routingUsage())
    } catch {
      /* provider health is informational */
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const startEdit = (item: AppConfigItem) => {
    setEditing(item.key)
    setDraftValue(item.value)
    setReason('')
    setMessage(null)
  }

  const save = async (key: string) => {
    if (!reason.trim()) {
      setMessage({
        tone: 'warn',
        text: 'A reason is required. Every configuration change is recorded in the audit trail.',
      })
      return
    }
    try {
      await api.updateConfig(key, draftValue, reason)
      setEditing(null)
      setMessage({ tone: 'ok', text: `${key} updated and recorded in the audit trail.` })
      await load()
    } catch (exception) {
      setMessage({
        tone: 'error',
        text:
          exception instanceof ApiError
            ? exception.message
            : (exception as Error).message,
      })
    }
  }

  const threshold = config.find((item) => item.key === THRESHOLD_KEY)

  return (
    <div style={{ padding: 16, display: 'grid', gap: 16, maxWidth: 1000, margin: '0 auto' }}>
      {message && (
        <div className={`alert alert--${message.tone === 'error' ? 'error' : message.tone}`}>
          <span aria-hidden="true">
            {message.tone === 'ok' ? '✓' : message.tone === 'warn' ? '⚑' : '⚠'}
          </span>
          <div className="alert__body">{message.text}</div>
        </div>
      )}

      {threshold && (
        <div className="card">
          <div className="card__header">Serviceability threshold</div>
          <div className="card__body">
            <div className="row" style={{ alignItems: 'flex-end', gap: 20 }}>
              <div>
                <div className="result-item__label">Current limit</div>
                <div className="result-item__value result-item__value--big">
                  {(Number(threshold.value) / 1000).toFixed(2)} KM
                </div>
                <div className="result-item__sub">
                  {Number(threshold.value).toLocaleString()} metres of actual road
                  distance
                </div>
              </div>
              <div className="grow" />
              {isAdmin ? (
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => startEdit(threshold)}
                >
                  Change threshold
                </button>
              ) : (
                <span className="subtle">
                  Only an administrator can change this value.
                </span>
              )}
            </div>
            <p className="subtle" style={{ marginTop: 12, marginBottom: 0 }}>
              A customer whose nearest existing service location is at or within this
              road distance is <strong>SERVICE AVAILABLE</strong>. A distance exactly
              equal to the limit is available; one metre beyond it is not.
            </p>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card__header">All configuration</div>
        <div className="card__body card__body--flush">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Value</th>
                  <th>Type</th>
                  <th>Description</th>
                  <th>Access</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {config.map((item) => (
                  <tr key={item.key}>
                    <td className="code">{item.key}</td>
                    <td>
                      {editing === item.key ? (
                        <div className="stack" style={{ minWidth: 260 }}>
                          <input
                            value={draftValue}
                            onChange={(event) => setDraftValue(event.target.value)}
                            aria-label={`New value for ${item.key}`}
                          />
                          <input
                            value={reason}
                            onChange={(event) => setReason(event.target.value)}
                            placeholder="Reason for this change (required)"
                            aria-label="Reason"
                          />
                          <div className="row">
                            <button
                              type="button"
                              className="btn btn--sm"
                              onClick={() => void save(item.key)}
                            >
                              Save
                            </button>
                            <button
                              type="button"
                              className="btn btn--secondary btn--sm"
                              onClick={() => setEditing(null)}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <span className="code">{item.value}</span>
                      )}
                      {item.minValue && (
                        <div className="subtle">
                          allowed {item.minValue}–{item.maxValue}
                        </div>
                      )}
                    </td>
                    <td className="subtle">{item.valueType}</td>
                    <td className="subtle" style={{ maxWidth: 340 }}>
                      {item.description}
                    </td>
                    <td>
                      {item.requiresAdmin ? (
                        <span className="status status--warning">
                          <span className="status__glyph" aria-hidden="true">
                            🔒
                          </span>
                          ADMIN ONLY
                        </span>
                      ) : (
                        <span className="status status--neutral">
                          <span className="status__glyph" aria-hidden="true">
                            •
                          </span>
                          SUPERVISOR
                        </span>
                      )}
                    </td>
                    <td>
                      {editing !== item.key &&
                        canWriteConfig &&
                        (!item.requiresAdmin || isAdmin) && (
                          <button
                            type="button"
                            className="btn btn--secondary btn--sm"
                            onClick={() => startEdit(item)}
                          >
                            Edit
                          </button>
                        )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card__header">Threshold change history</div>
        <div className="card__body card__body--flush">
          {history.length === 0 ? (
            <div className="empty">
              The threshold has not been changed since the system was installed.
            </div>
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>From</th>
                    <th>To</th>
                    <th>Changed by</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((entry) => (
                    <tr key={entry.id}>
                      <td className="subtle">
                        {new Date(entry.createdAt).toLocaleString()}
                      </td>
                      <td className="code">{entry.oldValue ?? '—'}</td>
                      <td className="code">{entry.newValue}</td>
                      <td>{entry.changedByLabel}</td>
                      <td>{entry.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {usage && (
        <div className="card">
          <div className="card__header">Routing provider</div>
          <div className="card__body">
            <div className="row" style={{ gap: 24 }}>
              <div>
                <div className="result-item__label">Provider</div>
                <div className="result-item__value">{usage.provider}</div>
              </div>
              <div>
                <div className="result-item__label">Health</div>
                <div>
                  <span
                    className={`status status--${usage.healthy ? 'available' : 'unavailable'}`}
                  >
                    <span className="status__glyph" aria-hidden="true">
                      {usage.healthy ? '✓' : '✕'}
                    </span>
                    {usage.healthy ? 'REACHABLE' : 'UNREACHABLE'}
                  </span>
                </div>
              </div>
              <div>
                <div className="result-item__label">Route cache hit rate</div>
                <div className="result-item__value">
                  {typeof usage.cache.hit_rate === 'number'
                    ? `${(usage.cache.hit_rate * 100).toFixed(1)}%`
                    : 'n/a'}
                </div>
              </div>
            </div>
            <p className="subtle" style={{ marginTop: 12, marginBottom: 0 }}>
              Per-operation call counts, latency histograms and error rates are exposed
              on <code>/metrics</code> for Prometheus. Routing API calls are the billed
              resource — watch <code>provider_elements_total</code>.
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
