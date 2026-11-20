import type { RouteOriginKind, RoutePreview, RoutePreviewResult } from '../types/api'

type RouteVisibility = Record<RouteOriginKind, boolean>
type SelectedRoutes = Record<RouteOriginKind, string | null>

interface Props {
  result: RoutePreviewResult | null
  loading: boolean
  error: string | null
  searchRadiusKm: number
  candidateLimitPerType: number
  visibility: RouteVisibility
  selectedRoutes: SelectedRoutes
  highlightedRouteId: string | null
  onSearchRadiusChange: (km: number) => void
  onCandidateLimitChange: (limit: number) => void
  onVisibilityChange: (kind: RouteOriginKind, visible: boolean) => void
  onRouteChange: (kind: RouteOriginKind, routeId: string) => void
  onHighlight: (routeId: string | null) => void
  onRefresh: () => void
  onClear: () => void
}

const statusText: Record<string, string> = {
  CALCULATED_UNVERIFIED: 'Road route calculated. Four-wheeler access is not legally verified.',
  ROUTE_GEOMETRY_UNAVAILABLE: 'Road distance is available, but this route has no drawable geometry.',
  REAL_ROUTING_PROVIDER_REQUIRED: 'A real driving-routes provider is required. Synthetic routes are never shown.',
  ROUTING_PROVIDER_NOT_CONFIGURED: 'Configure the backend road-routing provider and its credentials.',
  ROUTING_INVALID_REQUEST: 'The routing provider rejected the coordinates or request.',
  ROUTING_INVALID_RESPONSE: 'The routing provider returned an invalid response.',
  NO_NEARBY_LOCATION: 'No eligible origin was found within the selected search radius.',
  MISSING_COORDINATES: 'Existing customer records were found, but none have valid coordinates.',
  NO_DRIVING_ROUTE: 'No four-wheeler driving route is available for the selected origin.',
  ROUTE_SERVICE_UNAVAILABLE: 'The routing provider is temporarily unavailable. Please retry.',
  ROUTE_UNAVAILABLE: 'A driving route could not be calculated. Please retry.',
}

export function RoutePreviewPanel({
  result,
  loading,
  error,
  searchRadiusKm,
  candidateLimitPerType,
  visibility,
  selectedRoutes,
  highlightedRouteId,
  onSearchRadiusChange,
  onCandidateLimitChange,
  onVisibilityChange,
  onRouteChange,
  onHighlight,
  onRefresh,
  onClear,
}: Props) {
  const warehouseRoutes = routesFor(result, 'WAREHOUSE')
  const customerRoutes = routesFor(result, 'CUSTOMER')
  const warehouseRoute = warehouseRoutes.find((route) => route.id === selectedRoutes.WAREHOUSE) ?? warehouseRoutes[0] ?? null
  const customerRoute = customerRoutes.find((route) => route.id === selectedRoutes.CUSTOMER) ?? customerRoutes[0] ?? null
  const comparison = compareRoutes(warehouseRoute, customerRoute)

  return (
    <section className="route-preview" aria-live="polite">
      <header className="route-preview__header">
        <div>
          <h3>Four-wheeler route comparison</h3>
          <p>Both selected road routes are drawn independently on the map.</p>
        </div>
        <div className="route-preview__settings">
          <label>
            Search radius
            <select value={searchRadiusKm} onChange={(event) => onSearchRadiusChange(Number(event.target.value))}>
              {[5, 10, 25, 50].map((km) => <option key={km} value={km}>{km} km</option>)}
            </select>
          </label>
          <label>
            Candidates per type
            <select value={candidateLimitPerType} onChange={(event) => onCandidateLimitChange(Number(event.target.value))}>
              {[4, 8, 12, 20, 25].map((limit) => <option key={limit} value={limit}>{limit}</option>)}
            </select>
          </label>
          <button type="button" className="btn btn--secondary btn--sm" onClick={onRefresh} disabled={loading || !result}>
            {loading ? 'Calculating…' : 'Refresh routes'}
          </button>
          <button type="button" className="btn btn--secondary btn--sm" onClick={onClear} disabled={!result && !error}>
            Clear routes
          </button>
        </div>
      </header>

      {loading && (
        <div className="route-preview__loading" role="status">
          <span className="spinner" aria-hidden="true" /> Finding nearby origins and calculating actual road routes…
        </div>
      )}
      {!result && !loading && !error && (
        <p className="route-preview__empty">Enter an address or select a point on the map to compare routes.</p>
      )}
      {error && <p className="route-preview__error" role="alert">{error}</p>}
      {result && !loading && (
        <>
          {result.message && <p className="route-preview__empty">{result.message}</p>}
          <div className="route-preview__legend" aria-label="Route colours">
            <span><i className="route-preview__swatch route-preview__swatch--warehouse" />Blue · Warehouse → New Customer</span>
            <span><i className="route-preview__swatch route-preview__swatch--customer" />Red · Existing Customer → New Customer</span>
          </div>
          <div className="route-preview__routes">
            <RouteCard
              kind="WAREHOUSE"
              title="Warehouse to New Customer"
              routes={warehouseRoutes}
              selected={warehouseRoute}
              fallbackOrigin={result.nearestWarehouse}
              status={result.warehouseRouteStatus}
              message={result.warehouseRouteMessage}
              destination={result}
              visible={visibility.WAREHOUSE}
              highlightedRouteId={highlightedRouteId}
              onVisibilityChange={onVisibilityChange}
              onRouteChange={onRouteChange}
              onHighlight={onHighlight}
            />
            <RouteCard
              kind="CUSTOMER"
              title="Existing Customer to New Customer"
              routes={customerRoutes}
              selected={customerRoute}
              fallbackOrigin={result.nearestCustomer}
              status={result.customerRouteStatus}
              message={result.customerRouteMessage}
              destination={result}
              visible={visibility.CUSTOMER}
              highlightedRouteId={highlightedRouteId}
              onVisibilityChange={onVisibilityChange}
              onRouteChange={onRouteChange}
              onHighlight={onHighlight}
            />
          </div>
          <div className="route-preview__comparison" aria-live="polite">
            {comparison.shorter && <span>{comparison.shorter}</span>}
            {comparison.faster && <span>{comparison.faster}</span>}
            {comparison.distanceDifference && <span>{comparison.distanceDifference}</span>}
            {comparison.timeDifference && <span>{comparison.timeDifference}</span>}
            {(!warehouseRoute || !customerRoute) && <span>Comparison is partial because one route is unavailable.</span>}
          </div>
          <p className="route-preview__attribution">Driving routes: Google Maps Routes API. Map data © Google.</p>
        </>
      )}
    </section>
  )
}

function routesFor(result: RoutePreviewResult | null, kind: RouteOriginKind): RoutePreview[] {
  return result?.routes.filter((route) => route.origin.kind === kind) ?? []
}

function RouteCard({
  kind, title, routes, selected, fallbackOrigin, status, message, destination,
  visible, highlightedRouteId, onVisibilityChange, onRouteChange, onHighlight,
}: {
  kind: RouteOriginKind
  title: string
  routes: RoutePreview[]
  selected: RoutePreview | null
  fallbackOrigin: RoutePreviewResult['nearestWarehouse']
  status: string
  message: string | null
  destination: RoutePreviewResult
  visible: boolean
  highlightedRouteId: string | null
  onVisibilityChange: (kind: RouteOriginKind, visible: boolean) => void
  onRouteChange: (kind: RouteOriginKind, routeId: string) => void
  onHighlight: (routeId: string | null) => void
}) {
  const shortest = routes.reduce<RoutePreview | null>((best, route) => !best || route.distanceMeters < best.distanceMeters ? route : best, null)
  const fastest = routes.reduce<RoutePreview | null>((best, route) => {
    if (route.durationSeconds == null) return best
    return !best || best.durationSeconds == null || route.durationSeconds < best.durationSeconds ? route : best
  }, null)
  const selectedType = selected?.id === shortest?.id ? 'Shortest' : selected?.id === fastest?.id ? 'Fastest' : 'Alternative'
  const origin = selected?.origin ?? fallbackOrigin
  const routeStatus = selected?.availability ?? status
  const normalizedStatus = message ? status : routeStatus

  return (
    <article className={`route-preview__route route-preview__route--${kind.toLowerCase()}`}>
      <header className="route-preview__route-header">
        <h4>{title}</h4>
        {routes.length > 0 && (
          <label className="route-preview__toggle">
            <input type="checkbox" checked={visible} onChange={(event) => onVisibilityChange(kind, event.target.checked)} />
            Show route
          </label>
        )}
      </header>
      <dl>
        <dt>Starting location</dt><dd>{origin?.name ?? 'None found'}{origin?.address ? ` · ${origin.address}` : ''}</dd>
        <dt>Destination</dt><dd>{destination.destinationAddress || `${destination.destinationLatitude.toFixed(6)}, ${destination.destinationLongitude.toFixed(6)}`}</dd>
        <dt>Road distance</dt><dd>{selected ? `${(selected.distanceMeters / 1000).toFixed(2)} km` : '—'}</dd>
        <dt>Estimated driving time</dt><dd>{selected?.durationSeconds != null ? `${Math.max(1, Math.round(selected.durationSeconds / 60))} min` : '—'}</dd>
        <dt>Selected route type</dt><dd>{selected ? selectedType : '—'}</dd>
        <dt>Route status</dt><dd>{message ?? statusText[normalizedStatus] ?? normalizedStatus}</dd>
      </dl>
      {routes.length > 0 && (
        <>
          <label className="route-preview__alternative">
            Driving route alternative
            <select value={selected?.id ?? ''} onChange={(event) => onRouteChange(kind, event.target.value)}>
              {routes.map((route) => {
                const tags = [route.id === shortest?.id ? 'Shortest' : '', route.id === fastest?.id ? 'Fastest' : ''].filter(Boolean)
                const time = route.durationSeconds == null ? 'time unavailable' : `${Math.max(1, Math.round(route.durationSeconds / 60))} min`
                return <option key={route.id} value={route.id}>{tags.join(' / ') || 'Alternative'} · {(route.distanceMeters / 1000).toFixed(2)} km · {time}</option>
              })}
            </select>
          </label>
          <p className="route-preview__route-status">
            {selected?.geometry && selected.geometryFormat === 'encoded_polyline_5'
              ? 'Calculated road geometry · four-wheeler access unverified'
              : statusText[selected?.availability ?? status] ?? statusText[status] ?? status}
          </p>
          <div className="route-preview__alternatives" onMouseLeave={() => onHighlight(null)}>
            {routes.map((route, index) => (
              <button
                key={route.id}
                type="button"
                className={`route-preview__alternative-chip${route.id === selected?.id ? ' is-selected' : ''}${route.id === highlightedRouteId ? ' is-highlighted' : ''}`}
                onClick={() => onRouteChange(kind, route.id)}
                onMouseEnter={() => onHighlight(route.id)}
                aria-pressed={route.id === selected?.id}
              >
                {route.id === shortest?.id ? 'Shortest' : route.id === fastest?.id ? 'Fastest' : `Route ${index + 1}`}
              </button>
            ))}
          </div>
        </>
      )}
    </article>
  )
}

function compareRoutes(warehouse: RoutePreview | null, customer: RoutePreview | null) {
  if (!warehouse || !customer) return { shorter: null, faster: null, distanceDifference: null, timeDifference: null }
  const distanceDelta = Math.abs(warehouse.distanceMeters - customer.distanceMeters)
  const warehouseFaster = warehouse.durationSeconds != null && customer.durationSeconds != null
    ? warehouse.durationSeconds < customer.durationSeconds
    : null
  const timeDelta = warehouse.durationSeconds != null && customer.durationSeconds != null
    ? Math.abs(warehouse.durationSeconds - customer.durationSeconds)
    : null
  return {
    shorter: warehouse.distanceMeters === customer.distanceMeters
      ? 'Both selected routes have equal road distance.'
      : `${warehouse.distanceMeters < customer.distanceMeters ? 'Warehouse route' : 'Existing Customer route'} is shorter.`,
    faster: warehouseFaster == null
      ? 'Travel-time comparison is unavailable.'
      : warehouse.durationSeconds === customer.durationSeconds
        ? 'Both selected routes have equal estimated travel time.'
        : `${warehouseFaster ? 'Warehouse route' : 'Existing Customer route'} is faster.`,
    distanceDifference: `Distance difference: ${(distanceDelta / 1000).toFixed(2)} km.`,
    timeDifference: timeDelta == null
      ? null
      : `Estimated time difference: ${Math.max(1, Math.round(timeDelta / 60))} min.`,
  }
}
