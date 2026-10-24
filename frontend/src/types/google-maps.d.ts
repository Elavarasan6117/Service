export {}

declare global {
  namespace google.maps {
    interface LatLngLiteral {
      lat: number
      lng: number
    }

    interface LatLng {
      lat(): number
      lng(): number
    }

    interface MapMouseEvent {
      latLng: LatLng | null
    }

    interface MapsEventListener {
      remove(): void
    }

    interface MapOptions {
      center: LatLngLiteral
      zoom: number
      fullscreenControl?: boolean
      mapTypeControl?: boolean
      streetViewControl?: boolean
      clickableIcons?: boolean
    }

    class Map {
      constructor(mapDiv: HTMLElement, options: MapOptions)
      addListener(eventName: string, handler: (event: MapMouseEvent) => void): MapsEventListener
      panTo(latLng: LatLngLiteral): void
      getZoom(): number | undefined
      setZoom(zoom: number): void
      fitBounds(bounds: LatLngBounds, padding?: number): void
    }

    interface MarkerIcon {
      url: string
      scaledSize: Size
      anchor: Point
    }

    class Size {
      constructor(width: number, height: number)
    }

    class Point {
      constructor(x: number, y: number)
    }

    interface MarkerLabel {
      text: string
      color?: string
      fontSize?: string
      fontWeight?: string
    }

    interface MarkerOptions {
      map?: Map
      position: LatLngLiteral
      title?: string
      draggable?: boolean
      zIndex?: number
      icon?: MarkerIcon | string
      label?: MarkerLabel | string
    }

    class Marker {
      constructor(options: MarkerOptions)
      addListener(eventName: string, handler: (event: MapMouseEvent) => void): MapsEventListener
      getPosition(): LatLng | null
      setMap(map: Map | null): void
      setPosition(position: LatLngLiteral): void
      setDraggable(draggable: boolean): void
    }

    interface InfoWindowOptions {
      content?: string
    }

    class InfoWindow {
      constructor(options?: InfoWindowOptions)
      open(options?: { map?: Map; anchor?: Marker }): void
      setPosition(position: LatLng): void
    }

    interface PolylineOptions {
      map?: Map
      path: LatLngLiteral[]
      strokeColor?: string
      strokeOpacity?: number
      strokeWeight?: number
      clickable?: boolean
      icons?: { icon: { path: string; strokeOpacity?: number; scale?: number }; offset?: string; repeat?: string }[]
    }

    class Polyline {
      constructor(options: PolylineOptions)
      addListener(eventName: string, handler: (event: MapMouseEvent) => void): MapsEventListener
      setMap(map: Map | null): void
    }

    class LatLngBounds {
      extend(point: LatLngLiteral): void
    }

    enum SymbolPath {
      CIRCLE = 0,
    }
  }

  interface Window {
    google?: { maps: typeof google.maps }
  }
}
