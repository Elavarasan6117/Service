import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, ApiError } from '../api/client'
import { CustomerForm } from '../components/CustomerForm'
import { OperationsMap } from '../components/OperationsMap'
import { QuickAddLocationForm } from '../components/QuickAddLocationForm'
import { ServiceabilityResultPanel } from '../components/ServiceabilityResultPanel'
import { StatusBadge } from '../components/StatusBadge'
import { useLiveEvents } from '../hooks/useLiveEvents'
import type {
  Customer,
  DashboardMetrics,
  MapOperations,
  ServiceabilityCheck,
  ServiceabilityResult,
} from '../types/api'

interface Props {
  canCreate: boolean
  canAdjustLocation: boolean
  canDeleteChecks: boolean
  canManageWarehouses: boolean
  canManageServiceLocations: boolean
  onStreamState: (connected: boolean) => void
}

export function DashboardPage({
  canCreate,
  canAdjustLocation,
  canDeleteChecks,
  canManageWarehouses,
  canManageServiceLocations,
  onStreamState,
}: Props) {
  const [mapData, setMapData] = useState<MapOperations | null>(null)
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null)
  const [checks, setChecks] = useState<ServiceabilityCheck[]>([])
  const [result, setResult] = useState<ServiceabilityResult | null>(null)
  const [activeCustomer, setActiveCustomer] = useState<Customer | null>(null)
  // Two points, deliberately separate. `draftPoint` is what the MAP shows and
  // what the route is drawn from; `formPoint` is what the FORM is editing.
  // After a create they diverge: the map keeps the new customer's marker while
  // the form is cleared for the next entry.
  const [draftPoint, setDraftPoint] = useState<{
    latitude: number
    longitude: number
  } | null>(null)
  const [formPoint, setFormPoint] = useState<{
    latitude: number
    longitude: number
  } | null>(null)
  // Two more points, entirely separate from draftPoint/formPoint (the
  // customer-creation flow): one per quick-add form below, so the warehouse
  // and service location forms each keep their own pin on the shared map
  // instead of fighting over a single marker.
  const [warehouseDraftPoint, setWarehouseDraftPoint] = useState<{
    latitude: number
    longitude: number
  } | null>(null)
  const [serviceDraftPoint, setServiceDraftPoint] = useState<{
    latitude: number
    longitude: number
  } | null>(null)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pendingMove, setPendingMove] = useState(false)
  const [customerFormKey, setCustomerFormKey] = useState(0)
  const [selectedLocation, setSelectedLocation] = useState<{
    latitude: number
    longitude: number
    address: string
  } | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [map, dashboardMetrics, history] = await Promise.all([
        api.mapOperations({ customer_limit: 200 }),
        api.dashboardMetrics(),
        api.listChecks({ page_size: 15 }),
      ])
      setMapData(map)
      setMetrics(dashboardMetrics)
      setChecks(history.items)
      setError(null)
    } catch (exception) {
      setError((exception as Error).message)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // Live updates. A dashboard that needs a manual refresh to show a colleague's
  // decision is not a live map (brief section 7).
  const { connected } = useLiveEvents(true, (event) => {
    if (
      event.type === 'check.completed' ||
      event.type === 'check.error' ||
      event.type === 'customer.created' ||
      event.type === 'service_location.updated' ||
      event.type === 'import.completed'
    ) {
      void refresh()
    }
  })

  useEffect(() => {
    onStreamState(connected)
  }, [connected, onStreamState])

  const handleCreated = (response: {
    customer: Customer
    serviceability: ServiceabilityResult | null
  }) => {
    setActiveCustomer(response.customer)
    setResult(response.serviceability)
    if (response.customer.latitude != null && response.customer.longitude != null) {
      setDraftPoint({
        latitude: response.customer.latitude,
        longitude: response.customer.longitude,
      })
    }
    // The form has been cleared; do not let the map's point refill its
    // coordinate fields.
    setFormPoint(null)
    setSelectedLocation(null)
    setCustomerFormKey((key) => key + 1)
    setPendingMove(false)
    void refresh()
  }

  // Brief section 14: the operator drags the marker, then confirms, and the
  // check re-runs against the corrected position.
  const confirmMovedLocation = async () => {
    if (!activeCustomer || !draftPoint) return
    setChecking(true)
    setError(null)
    try {
      const updated = await api.updateCustomerLocation(
        activeCustomer.id,
        draftPoint.latitude,
        draftPoint.longitude,
        'Location confirmed on map by operator',
      )
      setResult(updated)
      setPendingMove(false)
      void refresh()
    } catch (exception) {
      setError(
        exception instanceof ApiError
          ? `${exception.message}${exception.requestId ? ` (request ${exception.requestId})` : ''}`
          : (exception as Error).message,
      )
    } finally {
      setChecking(false)
    }
  }

  const retryCheck = async () => {
    if (!activeCustomer) return
    setChecking(true)
    try {
      setResult(await api.runCheck(activeCustomer.id, true))
      void refresh()
    } catch (exception) {
      setError((exception as Error).message)
    } finally {
      setChecking(false)
    }
  }

  const deleteCheck = async (id: string) => {
    if (
      !window.confirm(
        'Delete this check and remove its customer marker from the map permanently?',
      )
    ) return
    try {
      const deletedCheck = checks.find((check) => check.id === id)
      await api.deleteCheck(id)
      if (deletedCheck?.customerId && deletedCheck.customerId === activeCustomer?.id) {
        setActiveCustomer(null)
        setDraftPoint(null)
        setFormPoint(null)
        setSelectedLocation(null)
        setResult(null)
        setPendingMove(false)
      }
      await refresh()
    } catch (exception) {
      setError((exception as Error).message)
    }
  }

  const handleMapLocation = useCallback(
    (latitude: number, longitude: number, selectingNewCustomer = false) => {
      setDraftPoint({ latitude, longitude })
      if (activeCustomer && !selectingNewCustomer) {
        setPendingMove(true)
        return
      }

      setActiveCustomer(null)
      setPendingMove(false)
      setFormPoint({ latitude, longitude })
      // Show the selected point immediately, even if reverse geocoding is
      // temporarily unavailable. The address is replaced when the lookup
      // succeeds, so the form never appears disconnected from the marker.
      setSelectedLocation({
        latitude,
        longitude,
        address: `Selected map location: ${latitude.toFixed(6)}, ${longitude.toFixed(6)}`,
      })
      void (async () => {
        try {
          const location = await api.reverseGeocode(latitude, longitude)
          setSelectedLocation({ latitude, longitude, address: location.formattedAddress })
          setResult(await api.previewServiceability({ latitude, longitude }))
        } catch (exception) {
          setError((exception as Error).message)
        }
      })()
    },
    [activeCustomer],
  )

  // Preview checks (no linked customer) are permanent audit history, but not
  // relevant to show alongside live customer activity on this dashboard.
  const linkedChecks = checks.filter((check) => check.customerId)

  return (
    <div className="layout">
      {/* ---------------- Left column ---------------- */}
      <div className="column">
        <div className="card">
          <div className="card__header">New customer</div>
          <div className="card__body">
            {canCreate ? (
              <CustomerForm
                key={customerFormKey}
                onCreated={handleCreated}
                onPreviewResult={setResult}
                onDraftPoint={(point) => {
                  setDraftPoint(point)
                  setFormPoint(point)
                  if (point === null) {
                    setActiveCustomer(null)
                    setPendingMove(false)
                  }
                }}
                formPoint={formPoint}
                selectedLocation={selectedLocation}
              />
            ) : (
              <p className="muted" style={{ margin: 0 }}>
                Your role has read-only access. Contact an administrator if you need to
                create customers.
              </p>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card__header">
            Serviceability result
            {metrics && (
              <span className="subtle" style={{ textTransform: 'none' }}>
                limit {(metrics.thresholdMeters / 1000).toFixed(2)} km
              </span>
            )}
          </div>
          <div className="card__body">
            {error && (
              <div className="alert alert--error" role="alert">
                <span aria-hidden="true">⚠</span>
                <div className="alert__body">{error}</div>
              </div>
            )}
            <ServiceabilityResultPanel
              result={result}
              loading={checking}
              onRetry={retryCheck}
            />
            {pendingMove && activeCustomer && canAdjustLocation && (
              <div className="row" style={{ marginTop: 13 }}>
                <button
                  type="button"
                  className="btn"
                  onClick={confirmMovedLocation}
                  disabled={checking}
                >
                  Confirm location & recalculate
                </button>
                <span className="subtle">
                  Marker moved to {draftPoint?.latitude.toFixed(6)},{' '}
                  {draftPoint?.longitude.toFixed(6)}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ---------------- Right column ---------------- */}
      <div className="column">
        <div className="card">
          <div className="card__header">Today</div>
          <div className="card__body">
            <div className="metrics">
              <Metric label="New customers" value={metrics?.newCustomers} />
              <Metric
                label="Service available"
                value={metrics?.serviceAvailable}
                tone="ok"
              />
              <Metric
                label="Not available"
                value={metrics?.serviceNotAvailable}
                tone="bad"
              />
              <Metric
                label="Verification req."
                value={metrics?.locationVerificationRequired}
                tone="warn"
              />
              <Metric
                label="Route errors"
                value={metrics?.routeCalculationErrors}
                tone="warn"
              />
              <Metric
                label="Avg. road distance"
                value={
                  metrics?.averageDistanceMeters != null
                    ? `${(metrics.averageDistanceMeters / 1000).toFixed(2)} km`
                    : '—'
                }
              />
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card__header">
            Live operations map
            <span className="subtle" style={{ textTransform: 'none' }}>
              {mapData
                ? `${mapData.serviceLocations.length} service locations · ${mapData.customers.length} customers`
                : 'loading…'}
            </span>
          </div>
          <div className="card__body card__body--flush">
            <OperationsMap
              data={mapData}
              result={result}
              draftPoint={draftPoint}
              draggable={canAdjustLocation}
              onDragEnd={(latitude, longitude) => {
                handleMapLocation(latitude, longitude)
              }}
              onMapClick={(latitude, longitude) => {
                handleMapLocation(latitude, longitude, true)
              }}
              editorPoints={[
                warehouseDraftPoint && {
                  id: 'warehouse-draft',
                  kind: 'warehouse' as const,
                  ...warehouseDraftPoint,
                },
                serviceDraftPoint && {
                  id: 'service-draft',
                  kind: 'service' as const,
                  ...serviceDraftPoint,
                },
              ].filter((point): point is NonNullable<typeof point> => point != null)}
              onEditorDragEnd={(id, latitude, longitude) => {
                if (id === 'warehouse-draft') setWarehouseDraftPoint({ latitude, longitude })
                else if (id === 'service-draft') setServiceDraftPoint({ latitude, longitude })
              }}
            />
          </div>
        </div>

        <div className="card">
          <div className="card__header">
            Recent serviceability checks
            <button
              type="button"
              className="btn btn--secondary btn--sm"
              onClick={() => void refresh()}
            >
              Refresh
            </button>
          </div>
          <div className="card__body card__body--flush">
            {/* Preview checks (no linked customer -- either a plain address
                lookup, or a customer that was since deleted) are permanent
                audit history, but not useful to show alongside live customer
                activity here. They're never removed from the database, only
                from this particular list. */}
            {linkedChecks.length === 0 ? (
              <div className="empty">No serviceability checks recorded yet.</div>
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Customer</th>
                      <th>Nearest service</th>
                      <th className="num">Road distance</th>
                      <th className="num">Limit</th>
                      <th>Result</th>
                      <th>By</th>
                      {canDeleteChecks && <th aria-label="Actions" />}
                    </tr>
                  </thead>
                  <tbody>
                    {linkedChecks.map((check) => (
                      <tr key={check.id}>
                        <td className="subtle">
                          {new Date(check.createdAt).toLocaleTimeString([], {
                            hour: '2-digit',
                            minute: '2-digit',
                          })}
                        </td>
                        <td>
                          <div>{check.customerName ?? '(preview)'}</div>
                          <div className="code subtle">{check.customerCode ?? '—'}</div>
                        </td>
                        <td className="code">{check.nearestServiceCode ?? '—'}</td>
                        <td className="num">
                          {check.distanceKm != null
                            ? `${check.distanceKm.toFixed(2)} km`
                            : '—'}
                        </td>
                        <td className="num subtle">
                          {(check.thresholdMeters / 1000).toFixed(2)} km
                        </td>
                        <td>
                          <ResultCell result={check.result} />
                        </td>
                        <td className="subtle">{check.createdByLabel}</td>
                        {canDeleteChecks && (
                          <td>
                            <button
                              type="button"
                              className="btn btn--secondary btn--sm"
                              onClick={() => void deleteCheck(check.id)}
                              title="Delete the customer and its map marker"
                              aria-label={`Delete customer ${check.customerName ?? ''}`}
                            >
                              Delete
                            </button>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card__header">
            Locations
            <Link to="/locations" className="btn btn--secondary btn--sm">
              Manage all locations
            </Link>
          </div>
          <div className="card__body">
            <QuickAddLocationForm
              kind="warehouse"
              canWrite={canManageWarehouses}
              point={warehouseDraftPoint}
              onPointChange={setWarehouseDraftPoint}
              onCreated={refresh}
            />
            <QuickAddLocationForm
              kind="service"
              canWrite={canManageServiceLocations}
              point={serviceDraftPoint}
              onPointChange={setServiceDraftPoint}
              onCreated={refresh}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string
  value: number | string | undefined
  tone?: 'ok' | 'bad' | 'warn'
}) {
  return (
    <div className={`metric${tone ? ` metric--${tone}` : ''}`}>
      <div className="metric__value">{value ?? '—'}</div>
      <div className="metric__label">{label}</div>
    </div>
  )
}

/** Audit results map onto the same visual vocabulary as customer statuses. */
function ResultCell({ result }: { result: string }) {
  const map: Record<string, { tone: string; glyph: string; label: string }> = {
    AVAILABLE: { tone: 'available', glyph: '✓', label: 'AVAILABLE' },
    NOT_AVAILABLE: { tone: 'unavailable', glyph: '✕', label: 'NOT AVAILABLE' },
    ROUTE_CALCULATION_ERROR: { tone: 'warning', glyph: '⚠', label: 'ROUTE ERROR' },
    NO_SERVICE_LOCATION_IN_RANGE: {
      tone: 'warning',
      glyph: '⚠',
      label: 'NONE IN RANGE',
    },
    LOCATION_VERIFICATION_REQUIRED: {
      tone: 'warning',
      glyph: '⚑',
      label: 'VERIFY LOCATION',
    },
  }
  const presentation = map[result] ?? { tone: 'neutral', glyph: '•', label: result }
  return (
    <span className={`status status--${presentation.tone}`}>
      <span className="status__glyph" aria-hidden="true">
        {presentation.glyph}
      </span>
      {presentation.label}
    </span>
  )
}

export { StatusBadge }
