/**
 * Google/OSRM encoded polyline decoder (precision 5).
 *
 * Both providers return route geometry in this format. Decoding it here rather
 * than pulling in a library keeps the bundle small and avoids a dependency for
 * forty lines of well-specified arithmetic.
 *
 * Format reference: developers.google.com/maps/documentation/utilities/polylinealgorithm
 */
export function decodePolyline(encoded: string, precision = 5): [number, number][] {
  if (!encoded) return []

  const factor = 10 ** precision
  const coordinates: [number, number][] = []
  let index = 0
  let lat = 0
  let lng = 0

  while (index < encoded.length) {
    let result = 1
    let shift = 0
    let byte: number

    do {
      byte = encoded.charCodeAt(index++) - 63 - 1
      result += byte << shift
      shift += 5
    } while (byte >= 0x1f && index < encoded.length)
    lat += result & 1 ? ~(result >> 1) : result >> 1

    result = 1
    shift = 0
    do {
      byte = encoded.charCodeAt(index++) - 63 - 1
      result += byte << shift
      shift += 5
    } while (byte >= 0x1f && index < encoded.length)
    lng += result & 1 ? ~(result >> 1) : result >> 1

    coordinates.push([lat / factor, lng / factor])
  }

  return coordinates
}
