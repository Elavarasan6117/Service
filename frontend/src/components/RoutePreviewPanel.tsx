import type { RoutePreview, RoutePreviewResult } from '../types/api'
import { decodePolyline } from '../lib/polyline'

interface Props {
  result: RoutePreviewResult | null
  loading: boolean
  error: string | null
  searchRadiusKm: number
  onSearchRadiusChange: (km: number) => void
}

const statusText: Record<string, string> = {
  CALCULATED_UNVERIFIED: 'Calculated · four-wheeler access unverified',
  ROUTE_GEOMETRY_UNAVAILABLE: 'Road distance calculated, but route geometry is unavailable to draw',
  REAL_ROUTING_PROVIDER_REQUIRED: 'A real routing provider is required; fake routing cannot supply road geometry',
  ROUTING_PROVIDER_NOT_CONFIGURED: 'Configure the server OSRM endpoint before calculating routes',
  ROUTING_INVALID_REQUEST: 'The routing service rejected the coordinates or request',
  ROUTING_INVALID_RESPONSE: 'The routing service returned an invalid response',
  NO_NEARBY_LOCATION: 'No nearby active location found',
  MISSING_COORDINATES: 'Existing customer records need valid latitude and longitude',
  NO_DRIVING_ROUTE: 'No driving route is available',
  ROUTE_SERVICE_UNAVAILABLE: 'OSRM is temporarily unavailable. Please retry.',
  ROUTE_UNAVAILABLE: 'Route service is temporarily unavailable',
}

export function RoutePreviewPanel({ result, loading, error, searchRadiusKm, onSearchRadiusChange }: Props) {
  const warehouseRoute = result?.routes.find((route) => route.origin.kind === 'WAREHOUSE') ?? null
  const customerRoute = result?.routes.find((route) => route.origin.kind === 'CUSTOMER') ?? null
  const comparison = warehouseRoute && customerRoute
    ? {
        shorter: warehouseRoute.distanceMeters === customerRoute.distanceMeters
          ? 'Both routes have the same road distance.'
          : `${warehouseRoute.distanceMeters < customerRoute.distanceMeters ? 'Route A' : 'Route B'} is shorter by ${(Math.abs(warehouseRoute.distanceMeters - customerRoute.distanceMeters) / 1000).toFixed(2)} km.`,
        quicker: warehouseRoute.durationSeconds == null || customerRoute.durationSeconds == null
          ? 'Travel-time comparison unavailable.'
          : warehouseRoute.durationSeconds === customerRoute.durationSeconds
            ? 'Both routes have the same estimated travel time.'
            : `${warehouseRoute.durationSeconds < customerRoute.durationSeconds ? 'Route A' : 'Route B'} is quicker by ${Math.max(1, Math.round(Math.abs(warehouseRoute.durationSeconds - customerRoute.durationSeconds) / 60))} min.`,
      }
    : null

  return (
    <section className="route-preview" aria-live="polite">
      <header className="route-preview__header">
        <div>
          <h3>Four-wheeler route comparison</h3>
          <p>Available road routes are drawn on the map independently</p>
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
          <span><i className="route-preview__swatch route-preview__swatch--customer" />Pink · Existing Customer → New Customer</span>
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
        <div className="route-preview__comparison" aria-live="polite">
          {comparison
            ? <><span>{comparison.shorter}</span><span>{comparison.quicker}</span></>
            : <span>Route comparison is unavailable until both road routes are calculated.</span>}
        </div>
        <p className="route-preview__attribution">
          Road routes: <a href="https://project-osrm.org/" target="_blank" rel="noreferrer">OSRM</a>
          {' · Map data © '}
          <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>
        </p>
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
  const displayStatus = route && (!route.geometry || route.geometryFormat !== 'encoded_polyline_5' || decodePolyline(route.geometry).length < 2)
    ? 'ROUTE_GEOMETRY_UNAVAILABLE'
    : route?.availability ?? status
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
      <p className="route-preview__status">{message ?? statusText[displayStatus] ?? statusText[status] ?? status}</p>
    </article>
  )
}
