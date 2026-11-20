import { useEffect, useRef, useState } from 'react'

import type { MapOperations, RouteOriginKind, RoutePreviewResult, ServiceabilityResult } from '../types/api'
import { STATUS_MARKER_COLOR } from './StatusBadge'
import { decodePolyline } from '../lib/polyline'

interface Props {
  data: MapOperations | null
  result: ServiceabilityResult | null
  routePreview?: RoutePreviewResult | null
  selectedRoutes?: Record<RouteOriginKind, string | null>
  routeVisibility?: Record<RouteOriginKind, boolean>
  highlightedRouteId?: string | null
  /** The point currently being assessed, which the operator can drag. */
  draftPoint: { latitude: number; longitude: number } | null
  draggable: boolean
  onDragEnd?: (latitude: number, longitude: number) => void
  onMapClick?: (latitude: number, longitude: number) => void
  editorPoints: { id: string; latitude: number; longitude: number; kind: 'warehouse' | 'service' }[]
  onEditorDragEnd?: (id: string, latitude: number, longitude: number) => void
}

type MarkerEntry = { marker: google.maps.Marker; info?: google.maps.InfoWindow }

let googleMapsPromise: Promise<void> | null = null

function loadGoogleMaps(): Promise<void> {
  if (window.google?.maps) return Promise.resolve()
  if (googleMapsPromise) return googleMapsPromise

  const apiKey = import.meta.env.VITE_GOOGLE_MAPS_API_KEY
  if (!apiKey) return Promise.reject(new Error('Google Maps key is missing. Set VITE_GOOGLE_MAPS_API_KEY in the frontend build environment.'))

  googleMapsPromise = new Promise((resolve, reject) => {
    const callbackName = '__serviceabilityGoogleMapsReady'
    const callbackHost = window as unknown as Record<string, () => void>
    const script = document.createElement('script')
    const timeout = window.setTimeout(() => {
      delete callbackHost[callbackName]
      script.remove()
      googleMapsPromise = null
      reject(new Error('Google Maps took too long to load. Check the browser key, Maps JavaScript API access, and network connection.'))
    }, 15000)
    callbackHost[callbackName] = () => {
      window.clearTimeout(timeout)
      delete callbackHost[callbackName]
      resolve()
    }
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}&callback=${callbackName}&loading=async`
    script.async = true
    script.defer = true
    script.onerror = () => {
      window.clearTimeout(timeout)
      delete callbackHost[callbackName]
      script.remove()
      googleMapsPromise = null
      reject(new Error('Google Maps could not load. Check that this key has Maps JavaScript API enabled and is allowed for this site.'))
    }
    document.head.appendChild(script)
  })
  return googleMapsPromise
}

function markerIcon(color: string, radius: number, square = false): google.maps.MarkerIcon {
  const size = radius * 2
  const shape = square
    ? `<rect x="1" y="1" width="${size - 2}" height="${size - 2}" rx="4" fill="${color}" stroke="#ffffff" stroke-width="2"/><text x="${radius}" y="${radius + 4}" text-anchor="middle" font-family="Arial,sans-serif" font-size="${Math.max(10, radius)}" font-weight="700" fill="#ffffff">W</text>`
    : `<circle cx="${radius}" cy="${radius}" r="${radius - 2}" fill="${color}" stroke="#ffffff" stroke-width="2"/>`
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${shape}</svg>`
  return {
    url: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
    scaledSize: new window.google!.maps.Size(size, size),
    anchor: new window.google!.maps.Point(radius, radius),
  }
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

export function OperationsMap({
  data,
  result,
  routePreview = null,
  selectedRoutes = { WAREHOUSE: null, CUSTOMER: null },
  routeVisibility = { WAREHOUSE: true, CUSTOMER: true },
  highlightedRouteId = null,
  draftPoint,
  draggable,
  onDragEnd,
  onMapClick,
  editorPoints,
  onEditorDragEnd,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<google.maps.Map | null>(null)
  const initialMapViewRef = useRef({
    center: { lat: data?.center.latitude ?? 13.0827, lng: data?.center.longitude ?? 80.2707 },
    zoom: data?.zoom ?? 12,
  })
  const mapListenerRef = useRef<google.maps.MapsEventListener | null>(null)
  const resizeObserverRef = useRef<ResizeObserver | null>(null)
  const initializationRef = useRef(false)
  const staticMarkersRef = useRef<MarkerEntry[]>([])
  const draftMarkerRef = useRef<google.maps.Marker | null>(null)
  const draftInfoRef = useRef<google.maps.InfoWindow | null>(null)
  const editorMarkersRef = useRef<Map<string, google.maps.Marker>>(new Map())
  const focusMarkersRef = useRef<MarkerEntry[]>([])
  const focusLinesRef = useRef<google.maps.Polyline[]>([])
  const routeMarkersRef = useRef<MarkerEntry[]>([])
  const routeLinesRef = useRef<google.maps.Polyline[]>([])
  const onMapClickRef = useRef(onMapClick)
  const onDragEndRef = useRef(onDragEnd)
  const onEditorDragEndRef = useRef(onEditorDragEnd)
  const [focusedServiceId, setFocusedServiceId] = useState<string | null>(null)
  const [mapError, setMapError] = useState<string | null>(null)
  const [mapReady, setMapReady] = useState(false)

  useEffect(() => { onMapClickRef.current = onMapClick }, [onMapClick])
  useEffect(() => { onDragEndRef.current = onDragEnd }, [onDragEnd])
  useEffect(() => { onEditorDragEndRef.current = onEditorDragEnd }, [onEditorDragEnd])

  useEffect(() => {
    let cancelled = false
    const handleGoogleMapsAuthFailure = () => {
      if (!cancelled) {
        setMapError(
          'Google Maps rejected this browser key. In Google Cloud Console, enable billing and Maps JavaScript API, then allow https://chennai-svc-frontend.onrender.com/* under website restrictions.',
        )
      }
    }
    window.gm_authFailure = handleGoogleMapsAuthFailure
    void loadGoogleMaps().then(() => {
      const container = containerRef.current
      if (cancelled) return
      if (!(container instanceof HTMLElement) || !container.isConnected) {
        throw new Error('The map container is not available. Reopen the page and try again.')
      }
      if (initializationRef.current || mapRef.current) return
      initializationRef.current = true
      const initialView = initialMapViewRef.current
      const map = new window.google!.maps.Map(container, {
        center: initialView.center,
        zoom: initialView.zoom,
        fullscreenControl: true,
        mapTypeControl: false,
        streetViewControl: false,
        clickableIcons: false,
      })
      mapRef.current = map
      setMapReady(true)
      mapListenerRef.current = map.addListener('click', (event) => {
        if (!event.latLng) return
        onMapClickRef.current?.(
          Number(event.latLng.lat().toFixed(6)),
          Number(event.latLng.lng().toFixed(6)),
        )
      })
      if (typeof ResizeObserver !== 'undefined') {
        const observer = new ResizeObserver((entries) => {
          if (!entries.some((entry) => entry.target === container) || !container.isConnected) return
          window.google?.maps.event.trigger(map, 'resize')
        })
        resizeObserverRef.current = observer
        observer.observe(container)
      }
    }).catch((error: unknown) => {
      initializationRef.current = false
      if (!cancelled) setMapError(error instanceof Error ? error.message : 'Google Maps could not load.')
    })

    const editorMarkers = editorMarkersRef.current
    return () => {
      cancelled = true
      resizeObserverRef.current?.disconnect()
      resizeObserverRef.current = null
      mapListenerRef.current?.remove()
      mapListenerRef.current = null
      if (window.gm_authFailure === handleGoogleMapsAuthFailure) {
        delete window.gm_authFailure
      }
      for (const entry of staticMarkersRef.current) entry.marker.setMap(null)
      for (const entry of focusMarkersRef.current) entry.marker.setMap(null)
      for (const line of focusLinesRef.current) line.setMap(null)
      for (const entry of routeMarkersRef.current) entry.marker.setMap(null)
      for (const line of routeLinesRef.current) line.setMap(null)
      draftMarkerRef.current?.setMap(null)
      draftInfoRef.current?.close()
      for (const marker of editorMarkers.values()) marker.setMap(null)
      staticMarkersRef.current = []
      focusMarkersRef.current = []
      focusLinesRef.current = []
      routeMarkersRef.current = []
      routeLinesRef.current = []
      draftMarkerRef.current = null
      draftInfoRef.current = null
      editorMarkers.clear()
      mapRef.current = null
      initializationRef.current = false
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !data) return
    for (const entry of staticMarkersRef.current) entry.marker.setMap(null)
    staticMarkersRef.current = []

    for (const warehouse of data.warehouses) {
      const marker = new window.google!.maps.Marker({
        map,
        position: { lat: warehouse.latitude, lng: warehouse.longitude },
        title: warehouse.warehouseName,
        icon: markerIcon('#4b2e83', 10, true),
        label: { text: 'W', color: '#ffffff', fontSize: '10px', fontWeight: '700' },
      })
      const info = new window.google!.maps.InfoWindow({
        content: `<strong>${escapeHtml(warehouse.warehouseName)}</strong><br/>Warehouse · ${escapeHtml(warehouse.warehouseCode)}`,
      })
      marker.addListener('click', () => info.open({ map, anchor: marker }))
      staticMarkersRef.current.push({ marker, info })
    }

    for (const location of data.serviceLocations) {
      if (location.latitude == null || location.longitude == null) continue
      const marker = new window.google!.maps.Marker({
        map,
        position: { lat: location.latitude, lng: location.longitude },
        title: location.locationName,
        icon: markerIcon('#1a5fa8', 7),
      })
      const info = new window.google!.maps.InfoWindow({
        content: `<strong>${escapeHtml(location.locationName)}</strong><br/>Existing service · ${escapeHtml(location.serviceCode)}<br/>${escapeHtml(location.serviceArea ?? location.area ?? '')}`,
      })
      marker.addListener('click', () => info.open({ map, anchor: marker }))
      staticMarkersRef.current.push({ marker, info })
    }

    for (const customer of data.customers) {
      const color = STATUS_MARKER_COLOR[customer.serviceStatus] ?? '#7d8896'
      const distance = customer.nearestServiceDistanceMeters != null
        ? `${(customer.nearestServiceDistanceMeters / 1000).toFixed(2)} km by road`
        : 'no road distance recorded'
      const marker = new window.google!.maps.Marker({
        map,
        position: { lat: customer.latitude, lng: customer.longitude },
        title: customer.customerName,
        icon: markerIcon(color, 9),
      })
      const info = new window.google!.maps.InfoWindow({
        content: `<strong>${escapeHtml(customer.customerName)}</strong><br/>${escapeHtml(customer.customerCode)}<br/><b>${escapeHtml(customer.serviceStatus.replace(/_/g, ' '))}</b><br/>${distance}`,
      })
      marker.addListener('click', () => info.open({ map, anchor: marker }))
      staticMarkersRef.current.push({ marker, info })
    }
  }, [data, mapReady])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (!draftPoint) {
      draftMarkerRef.current?.setMap(null)
      draftMarkerRef.current = null
      draftInfoRef.current?.close()
      draftInfoRef.current = null
      return
    }
    const position = { lat: draftPoint.latitude, lng: draftPoint.longitude }
    if (!draftMarkerRef.current) {
      const marker = new window.google!.maps.Marker({
        map,
        position,
        title: 'New customer — drag to correct, then Confirm',
        draggable,
        zIndex: 1000,
        icon: markerIcon('#E11D48', 11),
      })
      marker.addListener('dragend', () => {
        const point = marker.getPosition()
        if (point) onDragEndRef.current?.(Number(point.lat().toFixed(6)), Number(point.lng().toFixed(6)))
      })
      const info = new window.google!.maps.InfoWindow({ content: 'New customer location' })
      marker.addListener('click', () => info.open({ map, anchor: marker }))
      draftMarkerRef.current = marker
      draftInfoRef.current = info
    } else {
      draftMarkerRef.current.setMap(map)
      draftMarkerRef.current.setPosition(position)
      draftMarkerRef.current.setDraggable(draggable)
    }
    draftInfoRef.current?.setContent(
      `<strong>${escapeHtml(routePreview?.destinationAddress ?? 'New customer location')}</strong><br/>${draftPoint.latitude.toFixed(6)}, ${draftPoint.longitude.toFixed(6)}`,
    )
    map.panTo(position)
    if ((map.getZoom() ?? 0) < 16) map.setZoom(16)
  }, [draftPoint, draggable, mapReady, routePreview?.destinationAddress])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const markers = editorMarkersRef.current
    const seenIds = new Set(editorPoints.map((point) => point.id))
    for (const [id, marker] of markers) {
      if (!seenIds.has(id)) {
        marker.setMap(null)
        markers.delete(id)
      }
    }
    let lastPosition: google.maps.LatLngLiteral | null = null
    for (const point of editorPoints) {
      const position = { lat: point.latitude, lng: point.longitude }
      lastPosition = position
      const existing = markers.get(point.id)
      if (!existing) {
        const marker = new window.google!.maps.Marker({
          map,
          position,
          draggable: true,
          zIndex: 1000,
          title: point.kind === 'warehouse'
            ? 'New warehouse — drag to correct, then Save'
            : 'New service location — drag to correct, then Save',
          icon: markerIcon(point.kind === 'warehouse' ? '#4b2e83' : '#1a5fa8', point.kind === 'warehouse' ? 10 : 11, point.kind === 'warehouse'),
          label: point.kind === 'warehouse'
            ? { text: 'W', color: '#ffffff', fontSize: '10px', fontWeight: '700' }
            : undefined,
        })
        marker.addListener('dragend', () => {
          const dragged = marker.getPosition()
          if (dragged) onEditorDragEndRef.current?.(point.id, Number(dragged.lat().toFixed(6)), Number(dragged.lng().toFixed(6)))
        })
        markers.set(point.id, marker)
      } else {
        existing.setMap(map)
        existing.setPosition(position)
      }
    }
    if (lastPosition) map.panTo(lastPosition)
  }, [editorPoints, mapReady])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    for (const entry of focusMarkersRef.current) entry.marker.setMap(null)
    for (const line of focusLinesRef.current) line.setMap(null)
    focusMarkersRef.current = []
    focusLinesRef.current = []
    if (!result?.nearestServiceLocation || !draftPoint) return

    const nearest = result.nearestServiceLocation
    const color = result.status === 'AVAILABLE' ? '#0f7b3d' : '#b4232b'
    if (nearest.latitude != null && nearest.longitude != null) {
      const marker = new window.google!.maps.Marker({
        map,
        position: { lat: nearest.latitude, lng: nearest.longitude },
        title: nearest.name,
        zIndex: 900,
        icon: markerIcon(color, 11),
      })
      const info = new window.google!.maps.InfoWindow({
        content: `<strong>${escapeHtml(nearest.name)}</strong><br/>Nearest existing service · ${escapeHtml(nearest.serviceCode)}<br/><b>${nearest.distanceKm.toFixed(2)} km by road</b>`,
      })
      info.open({ map, anchor: marker })
      focusMarkersRef.current.push({ marker, info })
    }

    const points = result.route?.geometry ? decodePolyline(result.route.geometry) : []
    if (points.length > 1) {
      const path = points.map(([lat, lng]) => ({ lat, lng }))
      const line = new window.google!.maps.Polyline({
        map,
        path,
        strokeColor: color,
        strokeOpacity: 0.86,
        strokeWeight: 5,
        clickable: false,
      })
      focusLinesRef.current.push(line)
      const bounds = new window.google!.maps.LatLngBounds()
      for (const point of path) bounds.extend(point)
      map.fitBounds(bounds, 48)
    }
  }, [result, draftPoint, mapReady])

  useEffect(() => {
    const map = mapRef.current
    for (const entry of routeMarkersRef.current) entry.marker.setMap(null)
    for (const line of routeLinesRef.current) line.setMap(null)
    routeMarkersRef.current = []
    routeLinesRef.current = []
    if (!map || !routePreview) return

    const origins: { kind: RouteOriginKind; origin: NonNullable<RoutePreviewResult['nearestWarehouse']>; color: string; label: string }[] = []
    if (routePreview.nearestWarehouse) origins.push({ kind: 'WAREHOUSE', origin: routePreview.nearestWarehouse, color: '#2563EB', label: 'W' })
    if (routePreview.nearestCustomer) origins.push({ kind: 'CUSTOMER', origin: routePreview.nearestCustomer, color: '#E11D48', label: 'C' })
    for (const item of origins) {
      const marker = new window.google!.maps.Marker({
        map,
        position: { lat: item.origin.latitude, lng: item.origin.longitude },
        title: `${item.kind === 'WAREHOUSE' ? 'Warehouse' : 'Existing customer'} route origin: ${item.origin.name}`,
        zIndex: 940,
        icon: markerIcon(item.color, 10),
        label: { text: item.label, color: '#ffffff', fontWeight: '700', fontSize: '10px' },
      })
      const info = new window.google!.maps.InfoWindow({
        content: `<strong>${escapeHtml(item.origin.name)}</strong><br/>${item.kind === 'WAREHOUSE' ? 'Warehouse' : 'Existing customer'} route start<br/>${escapeHtml(item.origin.address ?? '')}`,
      })
      marker.addListener('click', () => info.open({ map, anchor: marker }))
      routeMarkersRef.current.push({ marker, info })
    }

    for (const kind of ['WAREHOUSE', 'CUSTOMER'] as const) {
      if (!routeVisibility[kind]) continue
      const route = routePreview.routes.find((item) => item.id === selectedRoutes[kind])
      if (!route?.geometry || route.geometryFormat !== 'encoded_polyline_5') continue
      const decoded = decodePolyline(route.geometry)
      if (decoded.length < 2) continue
      const path = decoded.map(([lat, lng]) => ({ lat, lng }))
      const color = kind === 'WAREHOUSE' ? '#2563EB' : '#E11D48'
      const selectedWeight = route.id === highlightedRouteId ? 9 : kind === 'WAREHOUSE' ? 8 : 6
      routeLinesRef.current.push(new window.google!.maps.Polyline({
        map,
        path,
        strokeColor: '#ffffff',
        strokeWeight: selectedWeight + 4,
        strokeOpacity: 0.92,
        clickable: false,
        zIndex: kind === 'WAREHOUSE' ? 20 : 21,
      }))
      routeLinesRef.current.push(new window.google!.maps.Polyline({
        map,
        path,
        strokeColor: color,
        strokeWeight: selectedWeight,
        strokeOpacity: 1,
        clickable: false,
        zIndex: kind === 'WAREHOUSE' ? 22 : 23,
      }))
    }
  }, [routePreview, selectedRoutes, routeVisibility, highlightedRouteId, mapReady])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !routePreview) return
    const bounds = new window.google!.maps.LatLngBounds()
    let hasPoint = false
    if (draftPoint) {
      bounds.extend({ lat: draftPoint.latitude, lng: draftPoint.longitude })
      hasPoint = true
    }
    for (const kind of ['WAREHOUSE', 'CUSTOMER'] as const) {
      if (!routeVisibility[kind]) continue
      const route = routePreview.routes.find((item) => item.id === selectedRoutes[kind])
      if (!route?.geometry || route.geometryFormat !== 'encoded_polyline_5') continue
      for (const [lat, lng] of decodePolyline(route.geometry)) {
        bounds.extend({ lat, lng })
        hasPoint = true
      }
    }
    if (hasPoint) map.fitBounds(bounds, 54)
  }, [routePreview, selectedRoutes, routeVisibility, draftPoint, mapReady])

  const fitPreviewRoutes = (onlyKind: RouteOriginKind | 'ALL') => {
    const map = mapRef.current
    if (!map || !routePreview) return
    const bounds = new window.google!.maps.LatLngBounds()
    let hasPoint = false
    if (draftPoint) {
      bounds.extend({ lat: draftPoint.latitude, lng: draftPoint.longitude })
      hasPoint = true
    }
    const kinds = onlyKind === 'ALL' ? ['WAREHOUSE', 'CUSTOMER'] as const : [onlyKind]
    for (const kind of kinds) {
      if (onlyKind === 'ALL' && !routeVisibility[kind]) continue
      const route = routePreview.routes.find((item) => item.id === selectedRoutes[kind])
      if (!route?.geometry || route.geometryFormat !== 'encoded_polyline_5') continue
      for (const [lat, lng] of decodePolyline(route.geometry)) {
        bounds.extend({ lat, lng })
        hasPoint = true
      }
    }
    if (hasPoint) map.fitBounds(bounds, 54)
  }

  const hasRouteGeometry = (kind: RouteOriginKind) => {
    const route = routePreview?.routes.find((item) => item.id === selectedRoutes[kind])
    return Boolean(route?.geometry && route.geometryFormat === 'encoded_polyline_5')
  }

  const serviceLocations = data?.serviceLocations.filter(
    (location) => location.latitude != null && location.longitude != null,
  ) ?? []

  return (
    <div className="map-shell">
      {serviceLocations.length > 0 && (
        <div className="map-destinations" aria-label="Service location destinations">
          <div className="map-destinations__heading">Service destinations</div>
          <div className="map-destinations__list">
            {serviceLocations.map((location) => (
              <button
                type="button"
                key={location.id}
                className={`map-destination${focusedServiceId === location.id ? ' map-destination--active' : ''}`}
                aria-pressed={focusedServiceId === location.id}
                onClick={() => {
                  setFocusedServiceId(location.id)
                  mapRef.current?.panTo({ lat: location.latitude!, lng: location.longitude! })
                  mapRef.current?.setZoom(15)
                }}
              >
                <span className="map-destination__name">{location.locationName}</span>
                <span className="map-destination__detail">{location.serviceArea || location.area || location.serviceCode}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div ref={containerRef} className="map-canvas" />
      {routePreview && (
        <div className="map-route-controls" aria-label="Route map controls">
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => fitPreviewRoutes('ALL')}>Fit visible routes</button>
          <button type="button" className="btn btn--secondary btn--sm" disabled={!hasRouteGeometry('WAREHOUSE')} onClick={() => fitPreviewRoutes('WAREHOUSE')}>Zoom Warehouse route</button>
          <button type="button" className="btn btn--secondary btn--sm" disabled={!hasRouteGeometry('CUSTOMER')} onClick={() => fitPreviewRoutes('CUSTOMER')}>Zoom Customer route</button>
        </div>
      )}
      {mapError && <div className="map-error" role="alert">{mapError}</div>}
      <div className="map-legend">
        <div className="map-legend__title">Legend</div>
        <div className="map-legend__row"><span className="map-legend__swatch map-legend__swatch--square" style={{ background: '#4b2e83' }} />Warehouse</div>
        <div className="map-legend__row"><span className="map-legend__swatch" style={{ background: '#1a5fa8' }} />Existing service location</div>
        <div className="map-legend__row"><span className="map-legend__swatch" style={{ background: '#E11D48' }} />New customer</div>
        <div className="map-legend__row"><span className="map-legend__swatch" style={{ background: '#0f7b3d' }} />Available / nearest</div>
        <div className="map-legend__row"><span className="map-legend__swatch" style={{ background: '#b4232b' }} />Not available</div>
        <div className="map-legend__row"><span className="map-legend__swatch" style={{ background: '#9a5b06' }} />Needs verification</div>
      </div>
    </div>
  )
}
