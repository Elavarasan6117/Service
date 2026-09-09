import { useEffect, useRef, useState } from 'react'

import { ApiError, api } from '../api/client'
import type { AddressSuggestion } from '../types/api'

type Kind = 'warehouse' | 'service'

function randomCode(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 8).toUpperCase()}`
}

/** 6-decimal string match -- tells "this is the point I just pushed echoing
 * back" from "this is a genuinely new drag on the map". */
function samePoint(
  a: { latitude: number; longitude: number } | null,
  lat: string,
  lng: string,
): boolean {
  if (!a || !lat || !lng) return false
  return (
    a.latitude.toFixed(6) === Number(lat).toFixed(6) &&
    a.longitude.toFixed(6) === Number(lng).toFixed(6)
  )
}

interface Props {
  kind: Kind
  canWrite: boolean
  /**
   * This form's own point on the shared map, owned by the parent. Unlike the
   * Locations page (one big list, one active card at a time), the Dashboard
   * always shows both the warehouse and the service quick-add forms at
   * once, so each needs its own permanent pin rather than a single shared
   * marker that jumps to whichever form was typed in most recently.
   */
  point: { latitude: number; longitude: number } | null
  onPointChange: (point: { latitude: number; longitude: number } | null) => void
  onCreated: () => void
}

/**
 * A minimal "name + address" form that geocodes straight onto the shared
 * Live Operations Map. The full record (code, city, pincode, edit, delete,
 * reactivate) lives on the separate Locations page -- this is add-only.
 */
export function QuickAddLocationForm({
  kind,
  canWrite,
  point,
  onPointChange,
  onCreated,
}: Props) {
  const [name, setName] = useState('')
  const [address, setAddress] = useState('')
  const [latitude, setLatitude] = useState('')
  const [longitude, setLongitude] = useState('')
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [geocoding, setGeocoding] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [justAdded, setJustAdded] = useState<string | null>(null)

  const sessionTokenRef = useRef(crypto.randomUUID())
  const debounceRef = useRef<number | undefined>(undefined)
  const geocodeDebounceRef = useRef<number | undefined>(undefined)
  const lastGeocodeAttemptRef = useRef('')

  // A drag on this form's own map marker, fed back in. Ignore it if it's
  // just an echo of the point this form itself just pushed via resolveAddress.
  useEffect(() => {
    if (!point || samePoint(point, latitude, longitude)) return
    const { latitude: lat, longitude: lng } = point
    setLatitude(lat.toFixed(6))
    setLongitude(lng.toFixed(6))
    void (async () => {
      try {
        const reverse = await api.reverseGeocode(lat, lng)
        setAddress(reverse.formattedAddress)
      } catch {
        // The pin is still placed correctly even if reverse geocoding fails.
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [point])

  const pushPoint = (lat: number, lng: number) => {
    onPointChange({ latitude: lat, longitude: lng })
  }

  const resolveAddress = async (value: string) => {
    if (!value.trim()) return
    lastGeocodeAttemptRef.current = value.trim()
    setGeocoding(true)
    setNotice(null)
    try {
      const result = await api.geocode(value)
      setLatitude(result.latitude.toFixed(6))
      setLongitude(result.longitude.toFixed(6))
      pushPoint(result.latitude, result.longitude)
      if (result.needsVerification) {
        setNotice(
          'This address matched only approximately. Drag the pin on the map to ' +
            'the exact location before saving.',
        )
      }
    } catch {
      setNotice(
        'Unable to locate this address. Please check the address or place the ' +
          'pin manually on the map.',
      )
    } finally {
      setGeocoding(false)
    }
  }

  const onAddressChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = event.target.value
    setAddress(value)
    setJustAdded(null)
    window.clearTimeout(debounceRef.current)
    window.clearTimeout(geocodeDebounceRef.current)
    if (value.trim().length < 3) {
      setSuggestions([])
      return
    }
    debounceRef.current = window.setTimeout(async () => {
      try {
        const results = await api.autocomplete(value, sessionTokenRef.current)
        setSuggestions(results)
        setShowSuggestions(true)
      } catch {
        setSuggestions([])
      }
    }, 300)
    if (value.trim().length >= 8) {
      geocodeDebounceRef.current = window.setTimeout(() => {
        void resolveAddress(value.trim())
      }, 900)
    }
  }

  const handleAddressBlur = () => {
    window.setTimeout(() => setShowSuggestions(false), 180)
    const value = address.trim()
    if (value.length >= 3 && value !== lastGeocodeAttemptRef.current) {
      window.clearTimeout(geocodeDebounceRef.current)
      void resolveAddress(value)
    }
  }

  const chooseSuggestion = async (suggestion: AddressSuggestion) => {
    setAddress(suggestion.description)
    setShowSuggestions(false)
    setSuggestions([])
    sessionTokenRef.current = crypto.randomUUID()
    await resolveAddress(suggestion.description)
  }

  const reset = () => {
    setName('')
    setAddress('')
    setLatitude('')
    setLongitude('')
    setNotice(null)
    setError(null)
    lastGeocodeAttemptRef.current = ''
  }

  const save = async () => {
    setError(null)
    if (!name.trim()) {
      setError('Name is required.')
      return
    }
    if (!latitude || !longitude) {
      setError('Search an address or place the pin on the map before saving.')
      return
    }
    setSaving(true)
    try {
      if (kind === 'warehouse') {
        await api.createWarehouse({
          warehouseCode: randomCode('WH'),
          warehouseName: name.trim(),
          address: address.trim() || undefined,
          latitude: Number(latitude),
          longitude: Number(longitude),
        })
      } else {
        await api.createServiceLocation({
          serviceCode: randomCode('SRV'),
          locationName: name.trim(),
          address: address.trim() || undefined,
          latitude: Number(latitude),
          longitude: Number(longitude),
        })
      }
      const addedName = name.trim()
      reset()
      setJustAdded(addedName)
      onPointChange(null)
      onCreated()
    } catch (exception) {
      setError(
        exception instanceof ApiError ? exception.message : (exception as Error).message,
      )
    } finally {
      setSaving(false)
    }
  }

  const fieldsDisabled = !canWrite || saving

  return (
    <div style={{ marginBottom: 20 }}>
      <div className="result-item__label" style={{ marginBottom: 8 }}>
        {kind === 'warehouse' ? 'Add warehouse location' : 'Add service location'}
      </div>
      {!canWrite ? (
        <p className="muted" style={{ margin: 0 }}>
          Your role has read-only access to{' '}
          {kind === 'warehouse' ? 'warehouses' : 'service locations'}.
        </p>
      ) : (
        <>
          {error && (
            <div className="alert alert--error" role="alert">
              <span aria-hidden="true">⚠</span>
              <div className="alert__body">{error}</div>
            </div>
          )}
          {notice && (
            <div className="alert alert--warn" role="status">
              <span aria-hidden="true">⚑</span>
              <div className="alert__body">{notice}</div>
            </div>
          )}
          {justAdded && (
            <div className="alert alert--ok" role="status">
              <span aria-hidden="true">✓</span>
              <div className="alert__body">
                Added &quot;{justAdded}&quot;. Edit the full details on the Locations page.
              </div>
            </div>
          )}

          <div className="field">
            <label>
              {kind === 'warehouse' ? 'Warehouse name' : 'Location name'}{' '}
              <span className="required">*</span>
            </label>
            <input
              value={name}
              onChange={(event) => {
                setName(event.target.value)
                setJustAdded(null)
              }}
              disabled={fieldsDisabled}
            />
          </div>

          <div className="field" style={{ position: 'relative' }}>
            <label>Address</label>
            <textarea
              value={address}
              onChange={onAddressChange}
              onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
              onBlur={handleAddressBlur}
              onKeyDown={(event) => {
                if (event.key === 'Escape') {
                  setShowSuggestions(false)
                  event.stopPropagation()
                }
              }}
              placeholder="Start typing an address in Chennai…"
              autoComplete="off"
              disabled={fieldsDisabled}
            />
            {showSuggestions && suggestions.length > 0 && (
              <div className="suggestions" role="listbox">
                {suggestions.map((suggestion) => (
                  <button
                    key={suggestion.placeId}
                    type="button"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => chooseSuggestion(suggestion)}
                  >
                    <div className="suggestions__main">
                      {suggestion.mainText || suggestion.description}
                    </div>
                    {suggestion.secondaryText && (
                      <div className="suggestions__secondary">{suggestion.secondaryText}</div>
                    )}
                  </button>
                ))}
              </div>
            )}
            <div className="field__hint">
              {kind === 'warehouse'
                ? 'Marks this warehouse on the map above in purple — drag its pin there to correct the exact spot.'
                : 'Marks this service location on the map above in blue — drag its pin there to correct the exact spot.'}
            </div>
          </div>

          <button
            type="button"
            className="btn btn--secondary btn--block"
            onClick={() => resolveAddress(address)}
            disabled={fieldsDisabled || geocoding || address.trim().length < 3}
            style={{ marginBottom: 13 }}
          >
            {geocoding ? <span className="spinner" aria-hidden="true" /> : '⌖'} Search location
          </button>

          <p className="subtle" style={{ margin: '0 0 12px' }}>
            {latitude && longitude
              ? `${latitude}, ${longitude} — see the pin on the map above`
              : 'No location pinned yet — search an address above.'}
          </p>

          <button type="button" className="btn" onClick={save} disabled={saving}>
            {saving && <span className="spinner" aria-hidden="true" />} Save
          </button>
        </>
      )}
    </div>
  )
}
