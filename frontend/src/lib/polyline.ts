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
  if (!encoded || !Number.isInteger(precision) || precision < 0 || precision > 8) return []

  const factor = 10 ** precision
  const coordinates: [number, number][] = []
  let index = 0
  let latitude = 0
  let longitude = 0

  const readDelta = (): number | null => {
    let result = 0
    let shift = 0
    let byte: number
    do {
      if (index >= encoded.length || shift > 30) return null
      byte = encoded.charCodeAt(index++) - 63
      if (byte < 0 || byte > 63) return null
      result += (byte & 0x1f) * 2 ** shift
      shift += 5
    } while (byte >= 0x20)

    return result % 2 === 1 ? -(result + 1) / 2 : result / 2
  }

  while (index < encoded.length) {
    const latitudeDelta = readDelta()
    const longitudeDelta = readDelta()
    if (latitudeDelta == null || longitudeDelta == null) return []

    latitude += latitudeDelta
    longitude += longitudeDelta
    const point: [number, number] = [latitude / factor, longitude / factor]
    if (
      !Number.isFinite(point[0]) || !Number.isFinite(point[1]) ||
      point[0] < -90 || point[0] > 90 || point[1] < -180 || point[1] > 180
    ) return []
    coordinates.push(point)
  }

  return coordinates
}
