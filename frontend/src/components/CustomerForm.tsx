import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError, api } from '../api/client'
import type {
  AddressSuggestion,
  CustomerCreateResponse,
  ServiceabilityResult,
} from '../types/api'

interface Props {
  disabled?: boolean
  onCreated: (response: CustomerCreateResponse) => void
  onPreviewResult: (result: ServiceabilityResult | null) => void
  onDraftPoint: (point: { latitude: number; longitude: number } | null) => void
  /**
   * The point the FORM is editing.
   *
   * Deliberately not the same as the map's draft point. After a customer is
   * created the map keeps showing that marker (and its route), but the form
   * has been cleared for the next entry -- so the parent passes null here
   * while the map still has a point. Sharing one value repopulated the
   * latitude/longitude of a freshly cleared form.
   */
  formPoint: { latitude: number; longitude: number } | null
  selectedLocation: {
    latitude: number
    longitude: number
    address: string
  } | null
}

interface FormState {
  customerCode: string
  customerName: string
  phone: string
  address: string
  area: string
  city: string
  pincode: string
  serviceType: string
  latitude: string
  longitude: string
}

const EMPTY: FormState = {
  customerCode: '',
  customerName: '',
  phone: '',
  address: '',
  area: '',
  city: '',
  pincode: '',
  serviceType: '',
  latitude: '',
  longitude: '',
}

const SERVICE_TYPES = ['DAILY', 'ALTERNATE_DAY', 'WEEKLY', 'ON_DEMAND']

export function CustomerForm({
  disabled,
  onCreated,
  onPreviewResult,
  onDraftPoint,
  formPoint,
  selectedLocation,
}: Props) {
  const [form, setForm] = useState<FormState>(EMPTY)
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [geocoding, setGeocoding] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // One Places session token per address search groups the keystrokes into a
  // single billable session rather than one request each.
  const sessionTokenRef = useRef(crypto.randomUUID())
  const debounceRef = useRef<number | undefined>(undefined)
  const geocodeDebounceRef = useRef<number | undefined>(undefined)
  const geocodeRequestRef = useRef(0)
  // The address text (trimmed) that the most recent geocode attempt -- in
  // flight or finished, successful or not -- was run for. Lets blur trigger
  // an immediate "finished typing" geocode without re-running it a second
  // time when the debounce already handled the same text moments earlier.
  const lastGeocodeAttemptRef = useRef('')

  // Keep the latitude/longitude fields in step when the operator drags the
  // marker on the map, so the form and the map never disagree.
  useEffect(() => {
    if (!formPoint) return
    setForm((previous) => ({
      ...previous,
      latitude: formPoint.latitude.toFixed(6),
      longitude: formPoint.longitude.toFixed(6),
    }))
  }, [formPoint])

  useEffect(() => {
    if (!selectedLocation) return
    geocodeRequestRef.current += 1
    window.clearTimeout(geocodeDebounceRef.current)
    setForm((previous) => ({
      ...previous,
      address: selectedLocation.address,
      latitude: selectedLocation.latitude.toFixed(6),
      longitude: selectedLocation.longitude.toFixed(6),
    }))
    setNotice(null)
  }, [selectedLocation])

  const update = (field: keyof FormState) => (
    event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>,
  ) => {
    setForm((previous) => ({ ...previous, [field]: event.target.value }))
  }

  const onAddressChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = event.target.value
    geocodeRequestRef.current += 1
    setForm((previous) => ({
      ...previous,
      address: value,
      latitude: '',
      longitude: '',
    }))
    onPreviewResult(null)
    onDraftPoint(null)

    window.clearTimeout(debounceRef.current)
    window.clearTimeout(geocodeDebounceRef.current)
    if (value.trim().length < 3) {
      setSuggestions([])
      return
    }
    // 300 ms debounce: Places is billed per session, and a request on every
    // keystroke is both slow and wasteful.
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

  const chooseSuggestion = async (suggestion: AddressSuggestion) => {
    geocodeRequestRef.current += 1
    setForm((previous) => ({
      ...previous,
      address: suggestion.description,
      latitude: '',
      longitude: '',
    }))
    onPreviewResult(null)
    onDraftPoint(null)
    setShowSuggestions(false)
    setSuggestions([])
    sessionTokenRef.current = crypto.randomUUID()
    await resolveAddress(suggestion.description)
  }

  const resolveAddress = useCallback(
    async (address: string) => {
      if (!address.trim()) return
      lastGeocodeAttemptRef.current = address.trim()
      const requestId = ++geocodeRequestRef.current
      setGeocoding(true)
      setError(null)
      setNotice(null)
      try {
        const result = await api.geocode(address)
        if (requestId !== geocodeRequestRef.current) return
        setForm((previous) => ({
          ...previous,
          latitude: result.latitude.toFixed(6),
          longitude: result.longitude.toFixed(6),
        }))
        onDraftPoint({ latitude: result.latitude, longitude: result.longitude })
        try {
          const preview = await api.previewServiceability({
            latitude: result.latitude,
            longitude: result.longitude,
            serviceType: form.serviceType || undefined,
          })
          if (requestId !== geocodeRequestRef.current) return
          onPreviewResult(preview)
        } catch {
          if (requestId !== geocodeRequestRef.current) return
          onPreviewResult(null)
        }
        if (result.needsVerification) {
          setNotice(
            'This address matched only approximately. Drag the marker to the exact ' +
              'location before saving.',
          )
        }
      } catch (exception) {
        if (requestId !== geocodeRequestRef.current) return
        // Not a failure of the form: the operator can still place the marker.
        setNotice(
          'Unable to locate this address. Please check the address or select the ' +
            'location manually on the map.',
        )
      } finally {
        setGeocoding(false)
      }
    },
    [form.serviceType, onDraftPoint, onPreviewResult],
  )

  const handleAddressBlur = () => {
    window.setTimeout(() => setShowSuggestions(false), 180)
    // "When the user finishes entering the address": geocode right away on
    // blur rather than only relying on the typing debounce, so leaving the
    // field (tabbing on, clicking elsewhere) always resolves the address
    // that's actually there -- including a short address the debounce's
    // length threshold would otherwise skip. Skipped if this exact text was
    // already the subject of the most recent attempt.
    const value = form.address.trim()
    if (value.length >= 3 && value !== lastGeocodeAttemptRef.current) {
      window.clearTimeout(geocodeDebounceRef.current)
      void resolveAddress(value)
    }
  }

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    setNotice(null)
    try {
      let latitude = form.latitude ? Number(form.latitude) : undefined
      let longitude = form.longitude ? Number(form.longitude) : undefined

      // Resolve on submit as well as from the explicit search button. This
      // ensures a newly entered address has a map point before it is created.
      if (latitude === undefined && longitude === undefined && form.address.trim()) {
        try {
          const resolved = await api.geocode(form.address.trim())
          latitude = resolved.latitude
          longitude = resolved.longitude
          setForm((previous) => ({
            ...previous,
            latitude: resolved.latitude.toFixed(6),
            longitude: resolved.longitude.toFixed(6),
          }))
          onDraftPoint({ latitude, longitude })
          try {
            const preview = await api.previewServiceability({
              latitude,
              longitude,
              serviceType: form.serviceType || undefined,
            })
            onPreviewResult(preview)
          } catch {
            onPreviewResult(null)
          }
        } catch {
          // The backend keeps the customer for manual map verification.
        }
      }

      const payload: Record<string, unknown> = {
        customerName: form.customerName.trim(),
        address: form.address.trim(),
        city: form.city.trim(),
      }
      if (form.customerCode.trim()) payload.customerCode = form.customerCode.trim()
      if (form.phone.trim()) payload.phone = form.phone.trim()
      if (form.area.trim()) payload.area = form.area.trim()
      if (form.pincode.trim()) payload.pincode = form.pincode.trim()
      if (form.serviceType) payload.serviceType = form.serviceType
      if (latitude !== undefined && longitude !== undefined) {
        payload.latitude = latitude
        payload.longitude = longitude
      }

      const response = await api.createCustomer(payload)
      onCreated(response)
      setForm({ ...EMPTY, city: '' })
      sessionTokenRef.current = crypto.randomUUID()
    } catch (exception) {
      setError(exception as Error)
    } finally {
      setSubmitting(false)
    }
  }

  const busy = Boolean(disabled) || submitting

  return (
    <form onSubmit={submit} noValidate>
      {error && (
        <div className="alert alert--error" role="alert">
          <span aria-hidden="true">⚠</span>
          <div className="alert__body">
            {error.message}
            {error instanceof ApiError && error.details.length > 0 && (
              <ul style={{ margin: '5px 0 0 16px', padding: 0 }}>
                {error.details.map((detail, index) => (
                  <li key={index}>
                    {detail.field ? `${detail.field}: ` : ''}
                    {detail.message ?? detail.issue}
                  </li>
                ))}
              </ul>
            )}
            {error instanceof ApiError && error.requestId && (
              <span className="alert__request-id">Request {error.requestId}</span>
            )}
          </div>
        </div>
      )}

      {notice && (
        <div className="alert alert--warn" role="status">
          <span aria-hidden="true">⚑</span>
          <div className="alert__body">{notice}</div>
        </div>
      )}

      <div className="field-row">
        <div className="field">
          <label htmlFor="customerCode">
            Customer ID <span className="subtle">(optional)</span>
          </label>
          <input
            id="customerCode"
            maxLength={64}
            value={form.customerCode}
            onChange={update('customerCode')}
            placeholder="CUST-1001"
            disabled={busy}
          />
        </div>
        <div className="field">
          <label htmlFor="phone">Phone number</label>
          <input
            id="phone"
            value={form.phone}
            onChange={update('phone')}
            placeholder="+91 98400 12345"
            disabled={busy}
          />
        </div>
      </div>

      <div className="field">
        <label htmlFor="customerName">
          Customer name <span className="required">*</span>
        </label>
        <input
          id="customerName"
          required
          value={form.customerName}
          onChange={update('customerName')}
          placeholder="ABC Foods"
          disabled={busy}
        />
      </div>

      <div className="field">
        <label htmlFor="address">
          Address <span className="required">*</span>
        </label>
        <textarea
          id="address"
          required
          value={form.address}
          onChange={onAddressChange}
          onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
          onBlur={handleAddressBlur}
          onKeyDown={(event) => {
            // Escape dismisses the suggestion list without clearing what has
            // been typed — the expected behaviour for a combobox, and it stops
            // the list obscuring the Area and Pincode fields beneath it.
            if (event.key === 'Escape') {
              setShowSuggestions(false)
              event.stopPropagation()
            }
          }}
          placeholder="Start typing an address in Chennai…"
          autoComplete="off"
          disabled={busy}
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
                  <div className="suggestions__secondary">
                    {suggestion.secondaryText}
                  </div>
                )}
              </button>
            ))}
          </div>
        )}
        <div className="field__hint">
          Address search runs on the server — no mapping API key is exposed to this
          browser.
        </div>
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="area">Area</label>
          <input
            id="area"
            value={form.area}
            onChange={update('area')}
            placeholder="City or locality"
            disabled={busy}
          />
        </div>
        <div className="field">
          <label htmlFor="pincode">Pincode</label>
          <input
            id="pincode"
            value={form.pincode}
            onChange={update('pincode')}
            placeholder="Postal code"
            inputMode="numeric"
            maxLength={6}
            disabled={busy}
          />
        </div>
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="city">City</label>
          <input id="city" value={form.city} onChange={update('city')} disabled={busy} />
        </div>
        <div className="field">
          <label htmlFor="serviceType">Service type</label>
          <select
            id="serviceType"
            value={form.serviceType}
            onChange={update('serviceType')}
            disabled={busy}
          >
            <option value="">Not specified</option>
            {SERVICE_TYPES.map((type) => (
              <option key={type} value={type}>
                {type.replace(/_/g, ' ')}
              </option>
            ))}
          </select>
        </div>
      </div>

      <button
        type="button"
        className="btn btn--secondary btn--block"
        onClick={() => resolveAddress(form.address)}
        disabled={busy || geocoding || form.address.trim().length < 3}
        style={{ marginBottom: 13 }}
      >
        {geocoding ? <span className="spinner" aria-hidden="true" /> : '⌖'} Search
        location
      </button>

      <div className="field-row">
        <div className="field">
          <label htmlFor="latitude">Latitude (optional)</label>
          <input
            id="latitude"
            value={form.latitude}
            onChange={(event) => {
              update('latitude')(event)
              const lat = Number(event.target.value)
              const lng = Number(form.longitude)
              if (Number.isFinite(lat) && Number.isFinite(lng) && form.longitude) {
                onDraftPoint({ latitude: lat, longitude: lng })
              }
            }}
            placeholder="13.085000"
            inputMode="decimal"
            disabled={busy}
          />
        </div>
        <div className="field">
          <label htmlFor="longitude">Longitude (optional)</label>
          <input
            id="longitude"
            value={form.longitude}
            onChange={(event) => {
              update('longitude')(event)
              const lat = Number(form.latitude)
              const lng = Number(event.target.value)
              if (Number.isFinite(lat) && Number.isFinite(lng) && form.latitude) {
                onDraftPoint({ latitude: lat, longitude: lng })
              }
            }}
            placeholder="80.210100"
            inputMode="decimal"
            disabled={busy}
          />
        </div>
      </div>

      {formPoint && (
        <p className="subtle" style={{ margin: '0 0 12px' }}>
          Marker placed. Drag it on the map to correct the position before saving.
        </p>
      )}

      <button type="submit" className="btn btn--block" disabled={busy}>
        {submitting ? (
          <>
            <span className="spinner" aria-hidden="true" /> Checking serviceability…
          </>
        ) : (
          'Create customer & check serviceability'
        )}
      </button>
    </form>
  )
}
