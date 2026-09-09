import { useEffect, useRef } from 'react'
import L from 'leaflet'

import type { MapOperations, ServiceabilityResult } from '../types/api'
import { STATUS_MARKER_COLOR } from './StatusBadge'
import { decodePolyline } from '../lib/polyline'

interface Props {
  data: MapOperations | null
  result: ServiceabilityResult | null
  /** The point currently being assessed, which the operator can drag. */
  draftPoint: { latitude: number; longitude: number } | null
  draggable: boolean
  onDragEnd?: (latitude: number, longitude: number) => void
  onMapClick?: (latitude: number, longitude: number) => void
  /**
   * The point(s) of whichever warehouse/service-location form(s) currently
   * have an address resolved in the Locations section, so typing an address
   * there marks it directly on this shared map instead of a private per-form
   * map -- coloured to match this same map's own legend (purple square =
   * warehouse, blue circle = service location) so it reads as "this is what
   * that marker will look like" rather than a new, unexplained colour.
   *
   * A list, not a single point: the Dashboard's two quick-add forms
   * (warehouse and service) are both visible at once and each needs its own
   * independent pin, not one shared marker that jumps to whichever form was
   * typed in most recently.
   */
  editorPoints: { id: string; latitude: number; longitude: number; kind: 'warehouse' | 'service' }[]
  onEditorDragEnd?: (id: string, latitude: number, longitude: number) => void
}

function circleIcon(color: string, size: number): L.DivIcon {
  return L.divIcon({
    className: '',
    html: `<div class="marker-pin" style="width:${size}px;height:${size}px;background:${color}"></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

const WAREHOUSE_ICON = L.divIcon({
  className: '',
  html:
    '<div class="marker-pin marker-pin--warehouse" style="width:26px;height:26px;background:#4b2e83">W</div>',
  iconSize: [26, 26],
  iconAnchor: [13, 13],
})

/**
 * Leaflet map over OpenStreetMap tiles.
 *
 * Deliberately not the Google Maps JS API: that would require shipping a Maps
 * key to the browser. Every Google call this system makes is server-side, and
 * the route drawn here is geometry the backend already fetched and returned.
 */
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
  const mapRef = useRef<L.Map | null>(null)
  const layersRef = useRef<{
    services: L.LayerGroup
    warehouses: L.LayerGroup
    customers: L.LayerGroup
    focus: L.LayerGroup
  } | null>(null)
  const draftMarkerRef = useRef<L.Marker | null>(null)
  const editorMarkersRef = useRef<Map<string, L.Marker>>(new Map())
  const tileLayerRef = useRef<L.TileLayer | null>(null)
  const onMapClickRef = useRef(onMapClick)

  useEffect(() => {
    onMapClickRef.current = onMapClick
  }, [onMapClick])

  // --- Create the map once ------------------------------------------------
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    const map = L.map(containerRef.current, {
      center: [13.0827, 80.2707],
      zoom: 12,
      zoomControl: true,
      preferCanvas: true, // markedly faster with thousands of markers
    })
    mapRef.current = map
    layersRef.current = {
      services: L.layerGroup().addTo(map),
      warehouses: L.layerGroup().addTo(map),
      customers: L.layerGroup().addTo(map),
      focus: L.layerGroup().addTo(map),
    }
    map.on('click', (event) => {
      onMapClickRef.current?.(
        Number(event.latlng.lat.toFixed(6)),
        Number(event.latlng.lng.toFixed(6)),
      )
    })

    return () => {
      map.remove()
      mapRef.current = null
      layersRef.current = null
      tileLayerRef.current = null
      draftMarkerRef.current = null
      editorMarkersRef.current.clear()
    }
  }, [])

  // --- Base layer and static markers -------------------------------------
  useEffect(() => {
    const map = mapRef.current
    const layers = layersRef.current
    if (!map || !layers || !data) return

    // The tile URL comes from the backend, so the base layer can only be built
    // once map data has arrived. Added once and then left alone.
    if (tileLayerRef.current === null) {
      tileLayerRef.current = L.tileLayer(data.tileUrl, {
        attribution: data.tileAttribution,
        maxZoom: 19,
      }).addTo(map)
    }

    layers.warehouses.clearLayers()
    for (const warehouse of data.warehouses) {
      L.marker([warehouse.latitude, warehouse.longitude], { icon: WAREHOUSE_ICON })
        .bindPopup(
          `<strong>${escapeHtml(warehouse.warehouseName)}</strong><br/>` +
            `Warehouse · ${escapeHtml(warehouse.warehouseCode)}`,
        )
        .addTo(layers.warehouses)
    }

    layers.services.clearLayers()
    for (const location of data.serviceLocations) {
      if (location.latitude == null || location.longitude == null) continue
      L.marker([location.latitude, location.longitude], {
        icon: circleIcon('#1a5fa8', 11),
      })
        .bindPopup(
          `<strong>${escapeHtml(location.locationName)}</strong><br/>` +
            `Existing service · ${escapeHtml(location.serviceCode)}<br/>` +
            `${escapeHtml(location.serviceArea ?? location.area ?? '')}`,
        )
        .addTo(layers.services)
    }

    layers.customers.clearLayers()
    for (const customer of data.customers) {
      const color = STATUS_MARKER_COLOR[customer.serviceStatus] ?? '#7d8896'
      const distance =
        customer.nearestServiceDistanceMeters != null
          ? `${(customer.nearestServiceDistanceMeters / 1000).toFixed(2)} km by road`
          : 'no road distance recorded'
      L.marker([customer.latitude, customer.longitude], {
        icon: circleIcon(color, 15),
      })
        .bindPopup(
          `<strong>${escapeHtml(customer.customerName)}</strong><br/>` +
            `${escapeHtml(customer.customerCode)}<br/>` +
            `<b>${customer.serviceStatus.replace(/_/g, ' ')}</b><br/>${distance}`,
        )
        .addTo(layers.customers)
    }
  }, [data])

  // --- Draggable draft marker --------------------------------------------
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    if (!draftPoint) {
      draftMarkerRef.current?.remove()
      draftMarkerRef.current = null
      return
    }

    const position: L.LatLngExpression = [draftPoint.latitude, draftPoint.longitude]
    if (!draftMarkerRef.current) {
      const marker = L.marker(position, {
        draggable,
        icon: circleIcon('#111827', 19),
        zIndexOffset: 1000,
      })
        .bindTooltip('New customer — drag to correct, then Confirm', {
          permanent: false,
          direction: 'top',
        })
        .addTo(map)
      marker.on('dragend', () => {
        const { lat, lng } = marker.getLatLng()
        onDragEnd?.(Number(lat.toFixed(6)), Number(lng.toFixed(6)))
      })
      draftMarkerRef.current = marker
    } else {
      draftMarkerRef.current.setLatLng(position)
      if (draggable) draftMarkerRef.current.dragging?.enable()
      else draftMarkerRef.current.dragging?.disable()
    }
    map.panTo(position, { animate: true })
  }, [draftPoint, draggable, onDragEnd])

  // --- Location-editor draft marker(s) (warehouse/service, from the
  // Locations section) -- entirely independent of the customer draftPoint
  // above, so editing a location and drafting a new customer never conflict.
  // A marker per point, keyed by id, so the Dashboard's warehouse and
  // service quick-add forms each keep their own pin instead of sharing one
  // that jumps to whichever form was typed in most recently. -------------
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const markers = editorMarkersRef.current

    const seenIds = new Set(editorPoints.map((point) => point.id))
    for (const [id, marker] of markers) {
      if (!seenIds.has(id)) {
        marker.remove()
        markers.delete(id)
      }
    }

    let lastPosition: L.LatLngExpression | null = null
    for (const point of editorPoints) {
      const position: L.LatLngExpression = [point.latitude, point.longitude]
      lastPosition = position
      const icon = point.kind === 'warehouse' ? WAREHOUSE_ICON : circleIcon('#1a5fa8', 19)
      const label =
        point.kind === 'warehouse'
          ? 'New warehouse — drag to correct, then Save'
          : 'New service location — drag to correct, then Save'

      const existing = markers.get(point.id)
      if (!existing) {
        const marker = L.marker(position, { draggable: true, icon, zIndexOffset: 1000 })
          .bindTooltip(label, { permanent: false, direction: 'top' })
          .addTo(map)
        marker.on('dragend', () => {
          const { lat, lng } = marker.getLatLng()
          onEditorDragEnd?.(point.id, Number(lat.toFixed(6)), Number(lng.toFixed(6)))
        })
        markers.set(point.id, marker)
      } else {
        existing.setLatLng(position)
        existing.setIcon(icon)
        existing.setTooltipContent(label)
      }
    }
    if (lastPosition) map.panTo(lastPosition, { animate: true })
  }, [editorPoints, onEditorDragEnd])

  // --- Route + nearest highlight -----------------------------------------
  useEffect(() => {
    const map = mapRef.current
    const layers = layersRef.current
    if (!map || !layers) return

    layers.focus.clearLayers()
    if (!result?.nearestServiceLocation || !draftPoint) return

    const nearest = result.nearestServiceLocation
    const isAvailable = result.status === 'AVAILABLE'
    const color = isAvailable ? '#0f7b3d' : '#b4232b'

    if (nearest.latitude != null && nearest.longitude != null) {
      L.marker([nearest.latitude, nearest.longitude], {
        icon: circleIcon(color, 19),
        zIndexOffset: 900,
      })
        .bindPopup(
          `<strong>${escapeHtml(nearest.name)}</strong><br/>` +
            `Nearest existing service · ${escapeHtml(nearest.serviceCode)}<br/>` +
            `<b>${nearest.distanceKm.toFixed(2)} km by road</b>`,
        )
        .addTo(layers.focus)
        .openPopup()
    }

    // The route geometry is what the routing provider actually returned, so
    // the line on the map is the road path the distance was measured along --
    // not a straight line between the two points.
    const points = result.route?.geometry
      ? decodePolyline(result.route.geometry)
      : []

    if (points.length > 1) {
      L.polyline(points, {
        color,
        weight: 5,
        opacity: 0.85,
        lineJoin: 'round',
      }).addTo(layers.focus)
      map.fitBounds(L.latLngBounds(points).pad(0.25), { maxZoom: 16 })
    } else if (nearest.latitude != null && nearest.longitude != null) {
      // No geometry returned. Draw a dashed straight line and label it as such
      // so nobody mistakes it for the measured route.
      L.polyline(
        [
          [draftPoint.latitude, draftPoint.longitude],
          [nearest.latitude, nearest.longitude],
        ],
        { color, weight: 3, opacity: 0.6, dashArray: '7 7' },
      )
        .bindTooltip(
          'Route geometry unavailable — straight line shown for reference only. ' +
            'The distance above is still the measured road distance.',
        )
        .addTo(layers.focus)
    }
  }, [result, draftPoint])

  return (
    <div className="map-shell">
      <div ref={containerRef} style={{ height: '100%', width: '100%' }} />
      <div className="map-legend">
        <div className="map-legend__title">Legend</div>
        <div className="map-legend__row">
          <span
            className="map-legend__swatch map-legend__swatch--square"
            style={{ background: '#4b2e83' }}
          />
          Warehouse
        </div>
        <div className="map-legend__row">
          <span className="map-legend__swatch" style={{ background: '#1a5fa8' }} />
          Existing service location
        </div>
        <div className="map-legend__row">
          <span className="map-legend__swatch" style={{ background: '#111827' }} />
          New customer
        </div>
        <div className="map-legend__row">
          <span className="map-legend__swatch" style={{ background: '#0f7b3d' }} />
          Available / nearest
        </div>
        <div className="map-legend__row">
          <span className="map-legend__swatch" style={{ background: '#b4232b' }} />
          Not available
        </div>
        <div className="map-legend__row">
          <span className="map-legend__swatch" style={{ background: '#9a5b06' }} />
          Needs verification
        </div>
      </div>
    </div>
  )
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}
