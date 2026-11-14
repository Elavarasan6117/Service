import type { RoutePreviewResult } from '../types/api'

interface Props {
  result: RoutePreviewResult | null
  loading: boolean
  selectedId: string | null
  onSelect: (id: string) => void
  searchRadiusKm: number
  onSearchRadiusChange: (km: number) => void
}

export function RoutePreviewPanel({ result, loading, selectedId, onSelect, searchRadiusKm, onSearchRadiusChange }: Props) {
  return (
    <section className="route-preview" aria-live="polite">
      <header className="route-preview__header">
        <div>
          <h3>Four-wheeler routes</h3>
          <p>Road routes from nearby warehouses and customers</p>
        </div>
        {loading && <span className="route-preview__loading">Calculating…</span>}
        <label className="route-preview__radius">Search radius
          <select value={searchRadiusKm} onChange={(event) => onSearchRadiusChange(Number(event.target.value))}>
            {[5, 10, 25, 50].map((km) => <option key={km} value={km}>{km} km</option>)}
          </select>
        </label>
      </header>
      {!result && !loading && <p className="muted">Select or geocode a new customer location to find routes.</p>}
      {result && <>
        <div className="route-preview__nearest">
          <span>Nearest warehouse: <b>{result.nearestWarehouse?.name ?? 'None found'}</b></span>
          <span>Nearest customer: <b>{result.nearestCustomer?.name ?? 'None found'}</b></span>
        </div>
        {result.routes.length === 0 ? <p className="muted">{result.message ?? 'No driving route is available for these nearby locations.'}</p> :
          <div className="route-preview__routes">
            {result.routes.map((route, index) => (
              <div key={route.id} role="button" tabIndex={0} className={`route-preview__route${selectedId === route.id ? ' is-selected' : ''}`} onClick={() => onSelect(route.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(route.id) } }}>
                <span className="route-preview__route-title">Route {index + 1} · {route.origin.kind === 'WAREHOUSE' ? 'Warehouse' : 'Customer'}</span>
                <span>{route.origin.name} → New customer</span>
                <span><b>{(route.distanceMeters / 1000).toFixed(2)} km</b> · {route.durationSeconds != null ? `${Math.max(1, Math.round(route.durationSeconds / 60))} min` : 'time unavailable'}</span>
                <span className="route-preview__status">Four-wheeler driving · Calculated, vehicle access unverified</span>
                <a href={`https://www.google.com/maps/dir/?api=1&origin=${route.origin.latitude},${route.origin.longitude}&destination=${result.destinationLatitude},${result.destinationLongitude}&travelmode=driving`} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>Open in Google Maps ↗</a>
              </div>
            ))}
          </div>}
      </>}
    </section>
  )
}
