import type { ServiceabilityResult } from '../types/api'
import { STATUS_PRESENTATION } from './StatusBadge'

interface Props {
  result: ServiceabilityResult | null
  loading: boolean
  onRetry?: () => void
}

const km = (metres: number) => (metres / 1000).toFixed(2)

/**
 * The decision, as the operations user reads it.
 *
 * Everything shown here comes from the backend. This component performs no
 * distance comparison of its own -- the backend is the source of truth for
 * serviceability, and the frontend renders its decision (brief section 33).
 */
export function ServiceabilityResultPanel({ result, loading, onRetry }: Props) {
  if (loading) {
    return (
      <div className="result-banner result-banner--info">
        <div className="result-banner__headline">
          <span className="spinner" aria-hidden="true" />
          CALCULATING
        </div>
        <p className="subtle" style={{ margin: '8px 0 0' }}>
          Measuring actual driving distance to nearby service locations…
        </p>
      </div>
    )
  }

  if (!result) {
    return (
      <div className="result-banner result-banner--neutral">
        <div className="result-banner__headline" style={{ color: 'var(--text-muted)' }}>
          <span aria-hidden="true">•</span> NO CHECK RUN YET
        </div>
        <p className="subtle" style={{ margin: '8px 0 0' }}>
          Create a customer or select one to see its serviceability decision.
        </p>
      </div>
    )
  }

  const presentation = STATUS_PRESENTATION[result.status] ?? STATUS_PRESENTATION.PENDING
  const nearest = result.nearestServiceLocation
  const decided = result.status === 'AVAILABLE' || result.status === 'NOT_AVAILABLE'

  return (
    <div className={`result-banner result-banner--${presentation.tone}`}>
      <div className="result-banner__headline">
        <span aria-hidden="true">{presentation.glyph}</span>
        {presentation.label}
      </div>

      {result.customerName && (
        <div className="result-item__sub" style={{ marginTop: 4 }}>
          {result.customerName}
          {result.customerId ? ` · ${result.customerId}` : ''}
        </div>
      )}

      {decided && nearest ? (
        <>
          <div className="result-grid">
            <div>
              <div className="result-item__label">Nearest existing service</div>
              <div className="result-item__value">{nearest.serviceCode}</div>
              <div className="result-item__sub">{nearest.name}</div>
            </div>
            <div>
              <div className="result-item__label">Driving distance</div>
              <div className="result-item__value result-item__value--big">
                {nearest.distanceKm.toFixed(2)} KM
              </div>
              <div className="result-item__sub">
                {nearest.distanceMeters.toLocaleString()} m by road
              </div>
            </div>
            <div>
              <div className="result-item__label">Maximum allowed</div>
              <div className="result-item__value">
                {km(result.thresholdMeters)} KM
              </div>
              <div className="result-item__sub">
                {result.thresholdMeters.toLocaleString()} m threshold
              </div>
            </div>
            <div>
              <div className="result-item__label">
                {result.status === 'AVAILABLE' ? 'Inside limit by' : 'Outside limit by'}
              </div>
              <div className="result-item__value">
                {result.marginMeters != null
                  ? `${Math.abs(result.marginMeters).toLocaleString()} m`
                  : '—'}
              </div>
              {result.route?.durationSeconds != null && (
                <div className="result-item__sub">
                  approx. {Math.round(result.route.durationSeconds / 60)} min drive
                </div>
              )}
            </div>
          </div>

          {result.reason && (
            <p style={{ margin: '12px 0 0', fontSize: 12.5 }}>{result.reason}</p>
          )}
        </>
      ) : (
        <div className="result-grid result-grid--full">
          <p style={{ margin: '10px 0 0', fontSize: 13 }}>
            {result.message ?? presentation.description}
          </p>
          {result.status === 'ROUTE_CALCULATION_ERROR' && (
            <p className="subtle" style={{ margin: 0 }}>
              This is <strong>not</strong> a coverage decision. The customer has not
              been rejected — we were unable to measure the driving distance.
            </p>
          )}
          {result.retryable && onRetry && (
            <div>
              <button type="button" className="btn btn--sm" onClick={onRetry}>
                Retry calculation
              </button>
            </div>
          )}
        </div>
      )}

      {result.degraded && (
        <p className="subtle" style={{ margin: '10px 0 0' }}>
          ⚠ Some candidate locations could not be routed to. The decision used the
          candidates that did resolve.
        </p>
      )}

      <div className="distance-basis">
        <span>
          Basis: <code>{result.distanceType}</code>
        </span>
        {result.routingProvider && <span>Provider: {result.routingProvider}</span>}
        <span>Candidates evaluated: {result.candidateCount}</span>
        {result.cacheHit && <span>Served from route cache</span>}
        {result.durationMs != null && <span>{result.durationMs} ms</span>}
        {result.checkId && (
          <span title="Audit record identifier">
            Audit: <code>{result.checkId.slice(0, 8)}</code>
          </span>
        )}
      </div>
    </div>
  )
}
