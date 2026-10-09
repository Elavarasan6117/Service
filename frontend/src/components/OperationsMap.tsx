import { useEffect, useRef, useState } from 'react'

import type { MapOperations, ServiceabilityResult } from '../types/api'
import { STATUS_MARKER_COLOR } from './StatusBadge'
import { decodePolyline } from '../lib/polyline'

interface Props {
  data: MapOperations | null
  result: ServiceabilityResult | null
  draftPoint: { latitude: number; longitude: number } | null
  draggable: boolean
  onDragEnd?: (latitude: number, longitude: number) => void
  onMapClick?: (latitude: number, longitude: number) => void
  editorPoints: { id: string; latitude: number; longitude: number; kind: 'warehouse' | 'service' }[]
  onEditorDragEnd?: (id: string, latitude: number, longitude: number) => void
}

type MarkerEntry = { marker: google.maps.Marker; info?: google.maps.InfoWindow }

let googleMapsPromise: Promise<void> | null = null

// Render's static-site blueprint may be deployed independently from its
// environment sync. Keep the explicitly configured browser key as a fallback
// so a missing build-time VITE variable cannot blank the live map.
const GOOGLE_MAPS_BROWSER_KEY = 'AIzaSyDhZ8Z-ZNqLmO35a9AuEwQTSf0nENURH90'

function loadGoogleMaps(): Promise<void> {
  if (window.google?.maps) return Promise.resolve()
  if (googleMapsPromise) return googleMapsPromise

  const apiKey = import.meta.env.VITE_GOOGLE_MAPS_API_KEY || GOOGLE_MAPS_BROWSER_KEY
  if (!apiKey) return Promise.reject(new Error('Google Maps key is missing. Set VITE_GOOGLE_MAPS_API_KEY in the frontend build environment.'))

  googleMapsPromise = new Promise((resolve, reject) => {
    const callbackName = '__serviceabilityGoogleMapsReady'
    const callbackHost = window as unknown as Record<string, () => void>
    callbackHost[callbackName] = () => {
      delete callbackHost[callbackName]
      resolve()
    }
    const script = document.createElement('script')
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}&callback=${callbackName}&loading=async`
    script.async = true
    script.defer = true
    script.onerror = () => {
      delete callbackHost[callbackName]
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
  draftPoint,
  draggable,
  onDragEnd,
  onMapClick,
  editorPoints,
  onEditorDragEnd,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<google.maps.Map | null>(null)
  const staticMarkersRef = useRef<MarkerEntry[]>([])
  const draftMarkerRef = useRef<google.maps.Marker | null>(null)
  const editorMarkersRef = useRef<Map<string, google.maps.Marker>>(new Map())
  const focusMarkersRef = useRef<MarkerEntry[]>([])
  const focusLinesRef = useRef<google.maps.Polyline[]>([])
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
    void loadGoogleMaps().then(() => {
      if (cancelled || !containerRef.current) return
      const map = new window.google!.maps.Map(containerRef.current, {
        center: { lat: data?.center.latitude ?? 13.0827, lng: data?.center.longitude ?? 80.2707 },
        zoom: data?.zoom ?? 12,
        fullscreenControl: true,
        mapTypeControl: false,
        streetViewControl: false,
        clickableIcons: false,
      })
      mapRef.current = map
      setMapReady(true)
      map.addListener('click', (event) => {
        if (!event.latLng) return
        onMapClickRef.current?.(
          Number(event.latLng.lat().toFixed(6)),
          Number(event.latLng.lng().toFixed(6)),
        )
      })
    }).catch((error: unknown) => {
      if (!cancelled) setMapError(error instanceof Error ? error.message : 'Google Maps could not load.')
    })

    return () => {
      cancelled = true
      for (const entry of staticMarkersRef.current) entry.marker.setMap(null)
      for (const entry of focusMarkersRef.current) entry.marker.setMap(null)
      for (const line of focusLinesRef.current) line.setMap(null)
      draftMarkerRef.current?.setMap(null)
      for (const marker of editorMarkersRef.current.values()) marker.setMap(null)
      staticMarkersRef.current = []
      focusMarkersRef.current = []
      focusLinesRef.current = []
      draftMarkerRef.current = null
      editorMarkersRef.current.clear()
      mapRef.current = null
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
        icon: markerIcon('#111827', 11),
      })
      marker.addListener('dragend', () => {
        const point = marker.getPosition()
        if (point) onDragEndRef.current?.(Number(point.lat().toFixed(6)), Number(point.lng().toFixed(6)))
      })
      draftMarkerRef.current = marker
    } else {
      draftMarkerRef.current.setMap(map)
      draftMarkerRef.current.setPosition(position)
      draftMarkerRef.current.setDraggable(draggable)
    }
    map.panTo(position)
    if ((map.getZoom() ?? 0) < 16) map.setZoom(16)
  }, [draftPoint, draggable, mapReady])

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
    } else if (nearest.latitude != null && nearest.longitude != null) {
      const line = new window.google!.maps.Polyline({
        map,
        path: [
          { lat: draftPoint.latitude, lng: draftPoint.longitude },
          { lat: nearest.latitude, lng: nearest.longitude },
        ],
        strokeColor: color,
        strokeOpacity: 0.7,
        strokeWeight: 3,
        icons: [{ icon: { path: 'M 0,-1 0,1', strokeOpacity: 1, scale: 4 }, offset: '0', repeat: '12px' }],
        clickable: true,
      })
      const info = new window.google!.maps.InfoWindow({
        content: 'Route geometry unavailable — straight line shown for reference only. Road distance is measured separately.',
      })
      line.addListener('click', (event) => {
        if (event.latLng) info.setPosition(event.latLng)
        info.open({ map })
      })
      focusLinesRef.current.push(line)
    }
  }, [result, draftPoint, mapReady])

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
      {mapError && <div className="map-error" role="alert">{mapError}</div>}
      <div className="map-legend">
        <div className="map-legend__title">Legend</div>
        <div className="map-legend__row"><span className="map-legend__swatch map-legend__swatch--square" style={{ background: '#4b2e83' }} />Warehouse</div>
        <div className="map-legend__row"><span className="map-legend__swatch" style={{ background: '#1a5fa8' }} />Existing service location</div>
        <div className="map-legend__row"><span className="map-legend__swatch" style={{ background: '#111827' }} />New customer</div>
        <div className="map-legend__row"><span className="map-legend__swatch" style={{ background: '#0f7b3d' }} />Available / nearest</div>
        <div className="map-legend__row"><span className="map-legend__swatch" style={{ background: '#b4232b' }} />Not available</div>
        <div className="map-legend__row"><span className="map-legend__swatch" style={{ background: '#9a5b06' }} />Needs verification</div>
      </div>
    </div>
  )
}
