import type { RoutePreview, RoutePreviewResult } from '../types/api'

interface Props {
  result: RoutePreviewResult | null
  loading: boolean
  error: string | null
  searchRadiusKm: number
  onSearchRadiusChange: (km: number) => void
}

const statusText: Record<string, string> = {
  CALCULATED_UNVERIFIED: 'Calculated · four-wheeler access unverified',
  NO_NEARBY_LOCATION: 'No nearby active location found',
  NO_DRIVING_ROUTE: 'No driving route is available',
  ROUTE_UNAVAILABLE: 'Route service is temporarily unavailable',
}

export function RoutePreviewPanel({ result, loading, error, searchRadiusKm, onSearchRadiusChange }: Props) {
  const warehouseRoute = result?.routes.find((route) => route.origin.kind === 'WAREHOUSE') ?? null
  const customerRoute = result?.routes.find((route) => route.origin.kind === 'CUSTOMER') ?? null

  return (
    <section className="route-preview" aria-live="polite">
      <header className="route-preview__header">
        <div>
          <h3>Four-wheeler route comparison</h3>
          <p>Both driving routes are shown on the map</p>
        </div>
        {loading && <span className="route-preview__loading">Calculating routes…</span>}
        <label className="route-preview__radius">Search radius
          <select value={searchRadiusKm} onChange={(event) => onSearchRadiusChange(Number(event.target.value))}>
            {[5, 10, 25, 50].map((km) => <option key={km} value={km}>{km} km</option>)}
          </select>
        </label>
      </header>
      {!result && !loading && <p className="muted">Select or geocode a new customer location to calculate routes.</p>}
      {!result && error && <p className="route-preview__error" role="alert">{error}</p>}
      {result && <>
        <div className="route-preview__legend" aria-label="Route colours">
          <span><i className="route-preview__swatch route-preview__swatch--warehouse" />Blue · Nearest Warehouse → New Customer</span>
          <span><i className="route-preview__swatch route-preview__swatch--customer" />Red · Existing Customer → New Customer</span>
        </div>
        <div className="route-preview__routes">
          <RouteCard
            title="Route A · Warehouse to New Customer"
            route={warehouseRoute}
            status={result.warehouseRouteStatus}
            message={result.warehouseRouteMessage}
            destination={result}
            color="warehouse"
          />
          <RouteCard
            title="Route B · Existing Customer to New Customer"
            route={customerRoute}
            status={result.customerRouteStatus}
            message={result.customerRouteMessage}
            destination={result}
            color="customer"
          />
        </div>
      </>}
    </section>
  )
}

function RouteCard({
  title, route, status, message, destination, color,
}: {
  title: string
  route: RoutePreview | null
  status: string
  message: string | null
  destination: RoutePreviewResult
  color: 'warehouse' | 'customer'
}) {
  const origin = route?.origin ?? (color === 'warehouse' ? destination.nearestWarehouse : destination.nearestCustomer)
  return (
    <article className={`route-preview__route route-preview__route--${color}`}>
      <h4>{title}</h4>
      <dl>
        <dt>Starting location</dt>
        <dd>{origin?.name ?? 'None found'}{origin?.address ? ` · ${origin.address}` : ''}</dd>
        <dt>Destination</dt>
        <dd>New customer location · {destination.destinationLatitude.toFixed(6)}, {destination.destinationLongitude.toFixed(6)}</dd>
        <dt>Road distance</dt>
        <dd>{route ? `${(route.distanceMeters / 1000).toFixed(2)} km` : '—'}</dd>
        <dt>Estimated driving time</dt>
        <dd>{route?.durationSeconds != null ? `${Math.max(1, Math.round(route.durationSeconds / 60))} min` : '—'}</dd>
      </dl>
      <p className="route-preview__status">{message ?? statusText[status] ?? status}</p>
    </article>
  )
}
