type Coordinates = { latitude: number | null; longitude: number | null }

function coordinatePair(point: Coordinates): string | null {
  const { latitude, longitude } = point
  if (
    latitude == null || longitude == null ||
    !Number.isFinite(latitude) || !Number.isFinite(longitude) ||
    latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180
  ) return null
  return `${latitude},${longitude}`
}

/** Build a Google Maps driving directions URL without making a routing API call. */
export function googleMapsDrivingDirectionsUrl(
  origin: Coordinates,
  destination: Coordinates & { address?: string | null },
): string | null {
  const originValue = coordinatePair(origin)
  const destinationValue = coordinatePair(destination) ?? destination.address?.trim()
  if (!originValue || !destinationValue) return null

  const params = new URLSearchParams({
    api: '1', origin: originValue, destination: destinationValue, travelmode: 'driving',
  })
  return `https://www.google.com/maps/dir/?${params.toString()}`
}
