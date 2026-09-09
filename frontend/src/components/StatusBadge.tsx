import type { ServiceStatus } from '../types/api'

/**
 * Status presentation, in one place.
 *
 * Brief section 9: "Do not rely only on colours. Always display text."
 * Every status therefore carries a text label AND a glyph AND a colour, and
 * this map is the only place any of the three is defined.
 */
export interface StatusPresentation {
  label: string
  glyph: string
  tone: 'available' | 'unavailable' | 'warning' | 'info' | 'neutral'
  description: string
}

export const STATUS_PRESENTATION: Record<ServiceStatus, StatusPresentation> = {
  AVAILABLE: {
    label: 'SERVICE AVAILABLE',
    glyph: '✓',
    tone: 'available',
    description: 'Within the road-distance limit of an existing service location.',
  },
  NOT_AVAILABLE: {
    label: 'SERVICE NOT AVAILABLE',
    glyph: '✕',
    tone: 'unavailable',
    description:
      'Outside the existing service coverage based on actual road distance.',
  },
  CALCULATING: {
    label: 'CALCULATING',
    glyph: '◌',
    tone: 'info',
    description: 'Measuring road distance to nearby service locations.',
  },
  PENDING: {
    label: 'PENDING',
    glyph: '•',
    tone: 'neutral',
    description: 'No serviceability check has run yet.',
  },
  LOCATION_VERIFICATION_REQUIRED: {
    label: 'LOCATION VERIFICATION REQUIRED',
    glyph: '⚑',
    tone: 'warning',
    description:
      'The address could not be confirmed. Place the marker on the map and confirm.',
  },
  ROUTE_CALCULATION_ERROR: {
    label: 'ROUTE CALCULATION ERROR',
    glyph: '⚠',
    tone: 'warning',
    description:
      'The driving distance could not be measured. This is not a coverage decision — retry.',
  },
  NO_SERVICE_LOCATION_CONFIGURED: {
    label: 'NO SERVICE LOCATIONS CONFIGURED',
    glyph: '⚠',
    tone: 'warning',
    description: 'There are no active service locations to compare against.',
  },
  PENDING_REVIEW: {
    label: 'PENDING REVIEW',
    glyph: '◑',
    tone: 'info',
    description: 'Held for manual review by a supervisor.',
  },
}

export function StatusBadge({ status }: { status: ServiceStatus }) {
  const presentation = STATUS_PRESENTATION[status] ?? STATUS_PRESENTATION.PENDING
  return (
    <span className={`status status--${presentation.tone}`} title={presentation.description}>
      <span className="status__glyph" aria-hidden="true">
        {presentation.glyph}
      </span>
      {presentation.label}
    </span>
  )
}

/** Map marker colours, kept in step with the status tones above. */
export const STATUS_MARKER_COLOR: Record<ServiceStatus, string> = {
  AVAILABLE: '#0f7b3d',
  NOT_AVAILABLE: '#b4232b',
  CALCULATING: '#1a5fa8',
  PENDING: '#7d8896',
  LOCATION_VERIFICATION_REQUIRED: '#9a5b06',
  ROUTE_CALCULATION_ERROR: '#c2410c',
  NO_SERVICE_LOCATION_CONFIGURED: '#9a5b06',
  PENDING_REVIEW: '#1a5fa8',
}
