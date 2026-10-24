import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import type { LayerGroup, Map as LeafletMap, Marker } from 'leaflet'

import type { MapOperations, ServiceabilityResult } from '../types/api'
import { decodePolyline } from '../lib/polyline'
import { STATUS_MARKER_COLOR } from './StatusBadge'

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

const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'

function markerIcon(color: string, radius: number, square = false): L.DivIcon {
  const size = radius * 2
  const shape = square
    ? `<rect x="1" y="1" width="${size - 2}" height="${size - 2}" rx="4" fill="${color}" stroke="#ffffff" stroke-width="2"/><text x="${radius}" y="${radius + 4}" text-anchor="middle" font-family="Arial,sans-serif" font-size="${Math.max(10, radius)}" font-weight="700" fill="#ffffff">W</text>`
    : `<circle cx="${radius}" cy="${radius}" r="${radius - 2}" fill="${color}" stroke="#ffffff" stroke-width="2"/>`
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${shape}</svg>`
  return L.divIcon({
    className: 'map-marker-icon',
    html: svg,
    iconSize: [size, size],
    iconAnchor: [radius, radius],
  })
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
  const mapRef = useRef<LeafletMap | null>(null)
  const staticMarkersRef = useRef<LayerGroup | null>(null)
  const focusMarkersRef = useRef<LayerGroup | null>(null)
  const focusLinesRef = useRef<LayerGroup | null>(null)
  const draftMarkerRef = useRef<Marker | null>(null)
  const editorMarkersRef = useRef<Map<string, Marker>>(new Map())
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
    if (!containerRef.current) return

    const map = L.map(containerRef.current, {
      center: [data?.center.latitude ?? 13.0827, data?.center.longitude ?? 80.2707],
      zoom: data?.zoom ?? 12,
      zoomControl: true,
      scrollWheelZoom: true,
    })
    mapRef.current = map
    staticMarkersRef.current = L.layerGroup().addTo(map)
    focusMarkersRef.current = L.layerGroup().addTo(map)
    focusLinesRef.current = L.layerGroup().addTo(map)

    const tiles = L.tileLayer(TILE_URL, {
      maxZoom: 19,
      attribution: TILE_ATTRIBUTION,
    }).addTo(map)
    tiles.on('tileerror', () => setMapError('Map tiles could not be loaded. Check your internet connection and try again.'))
    tiles.on('load', () => setMapError(null))

    map.on('click', (event) => {
      onMapClickRef.current?.(
        Number(event.latlng.lat.toFixed(6)),
        Number(event.latlng.lng.toFixed(6)),
      )
    })
    setMapReady(true)

    return () => {
      map.remove()
      mapRef.current = null
      staticMarkersRef.current = null
      focusMarkersRef.current = null
      focusLinesRef.current = null
      draftMarkerRef.current = null
      editorMarkersRef.current.clear()
      setMapReady(false)
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    const markers = staticMarkersRef.current
    if (!map || !markers || !data) return
    markers.clearLayers()

    for (const warehouse of data.warehouses) {
      L.marker([warehouse.latitude, warehouse.longitude], {
        icon: markerIcon('#4b2e83', 10, true),
        title: warehouse.warehouseName,
      }).bindPopup(`<strong>${escapeHtml(warehouse.warehouseName)}</strong><br/>Warehouse · ${escapeHtml(warehouse.warehouseCode)}`).addTo(markers)
    }

    for (const location of data.serviceLocations) {
      if (location.latitude == null || location.longitude == null) continue
      L.marker([location.latitude, location.longitude], {
        icon: markerIcon('#1a5fa8', 7),
        title: location.locationName,
      }).bindPopup(`<strong>${escapeHtml(location.locationName)}</strong><br/>Existing service · ${escapeHtml(location.serviceCode)}<br/>${escapeHtml(location.serviceArea ?? location.area ?? '')}`).addTo(markers)
    }

    for (const customer of data.customers) {
      const color = STATUS_MARKER_COLOR[customer.serviceStatus] ?? '#7d8896'
      const distance = customer.nearestServiceDistanceMeters != null
        ? `${(customer.nearestServiceDistanceMeters / 1000).toFixed(2)} km by road`
        : 'no road distance recorded'
      L.marker([customer.latitude, customer.longitude], {
        icon: markerIcon(color, 9),
        title: customer.customerName,
      }).bindPopup(`<strong>${escapeHtml(customer.customerName)}</strong><br/>${escapeHtml(customer.customerCode)}<br/><b>${escapeHtml(customer.serviceStatus.replace(/_/g, ' '))}</b><br/>${distance}`).addTo(markers)
    }
  }, [data, mapReady])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (!draftPoint) {
      if (draftMarkerRef.current) map.removeLayer(draftMarkerRef.current)
      draftMarkerRef.current = null
      return
    }

    const position: L.LatLngExpression = [draftPoint.latitude, draftPoint.longitude]
    if (!draftMarkerRef.current) {
      const marker = L.marker(position, {
        icon: markerIcon('#111827', 11),
        draggable,
        zIndexOffset: 1000,
        title: 'New customer — drag to correct, then Confirm',
      }).addTo(map)
      marker.on('dragend', () => {
        const point = marker.getLatLng()
        onDragEndRef.current?.(Number(point.lat.toFixed(6)), Number(point.lng.toFixed(6)))
      })
      draftMarkerRef.current = marker
    } else {
      draftMarkerRef.current.addTo(map).setLatLng(position)
      if (draggable) draftMarkerRef.current.dragging?.enable()
      else draftMarkerRef.current.dragging?.disable()
    }
    map.panTo(position)
    if (map.getZoom() < 16) map.setZoom(16)
  }, [draftPoint, draggable, mapReady])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const markers = editorMarkersRef.current
    const seenIds = new Set(editorPoints.map((point) => point.id))
    for (const [id, marker] of markers) {
      if (!seenIds.has(id)) {
        map.removeLayer(marker)
        markers.delete(id)
      }
    }

    let lastPosition: L.LatLngExpression | null = null
    for (const point of editorPoints) {
      const position: L.LatLngExpression = [point.latitude, point.longitude]
      lastPosition = position
      let marker = markers.get(point.id)
      if (!marker) {
        marker = L.marker(position, {
          icon: markerIcon(point.kind === 'warehouse' ? '#4b2e83' : '#1a5fa8', point.kind === 'warehouse' ? 10 : 11, point.kind === 'warehouse'),
          draggable: true,
          zIndexOffset: 1000,
          title: point.kind === 'warehouse'
            ? 'New warehouse — drag to correct, then Save'
            : 'New service location — drag to correct, then Save',
        }).addTo(map)
        marker.on('dragend', () => {
          const dragged = marker!.getLatLng()
          onEditorDragEndRef.current?.(point.id, Number(dragged.lat.toFixed(6)), Number(dragged.lng.toFixed(6)))
        })
        markers.set(point.id, marker)
      } else {
        marker.addTo(map).setLatLng(position)
      }
    }
    if (lastPosition) map.panTo(lastPosition)
  }, [editorPoints, mapReady])

  useEffect(() => {
    const map = mapRef.current
    const markers = focusMarkersRef.current
    const lines = focusLinesRef.current
    if (!map || !markers || !lines) return
    markers.clearLayers()
    lines.clearLayers()
    if (!result?.nearestServiceLocation || !draftPoint) return

    const nearest = result.nearestServiceLocation
    const color = result.status === 'AVAILABLE' ? '#0f7b3d' : '#b4232b'
    if (nearest.latitude != null && nearest.longitude != null) {
      L.marker([nearest.latitude, nearest.longitude], {
        icon: markerIcon(color, 11),
        zIndexOffset: 900,
        title: nearest.name,
      }).bindPopup(`<strong>${escapeHtml(nearest.name)}</strong><br/>Nearest existing service · ${escapeHtml(nearest.serviceCode)}<br/><b>${nearest.distanceKm.toFixed(2)} km by road</b>`)
        .addTo(markers).openPopup()
    }

    const points = result.route?.geometry ? decodePolyline(result.route.geometry) : []
    if (points.length > 1) {
      const routeLine = L.polyline(points, {
        color,
        opacity: 0.86,
        weight: 5,
        interactive: false,
      }).addTo(lines)
      map.fitBounds(routeLine.getBounds(), { padding: [48, 48] })
    } else if (nearest.latitude != null && nearest.longitude != null) {
      L.polyline([
        [draftPoint.latitude, draftPoint.longitude],
        [nearest.latitude, nearest.longitude],
      ], {
        color,
        opacity: 0.7,
        weight: 3,
        dashArray: '5 8',
      }).bindPopup('Route geometry unavailable — straight line shown for reference only. Road distance is measured separately.')
        .addTo(lines)
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
                  mapRef.current?.panTo([location.latitude!, location.longitude!])
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
