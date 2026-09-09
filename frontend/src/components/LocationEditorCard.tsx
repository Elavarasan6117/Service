import { useEffect, useRef, useState } from 'react'

import { ApiError, api } from '../api/client'
import type { AddressSuggestion, ServiceLocation, Warehouse } from '../types/api'

type Kind = 'warehouse' | 'service'
type Location = Warehouse | ServiceLocation

interface FormState {
  code: string
  name: string
  address: string
  area: string
  city: string
  pincode: string
  serviceArea: string
  latitude: string
  longitude: string
}

const EMPTY: FormState = {
  code: '',
  name: '',
  address: '',
  area: '',
  city: 'Chennai',
  pincode: '',
  serviceArea: '',
  latitude: '',
  longitude: '',
}

function toForm(kind: Kind, location: Location): FormState {
  if (kind === 'warehouse') {
    const w = location as Warehouse
    return {
      ...EMPTY,
      code: w.warehouseCode,
      name: w.warehouseName,
      address: w.address ?? '',
      city: w.city ?? 'Chennai',
      pincode: w.pincode ?? '',
      latitude: String(w.latitude),
      longitude: String(w.longitude),
    }
  }
  const s = location as ServiceLocation
  return {
    ...EMPTY,
    code: s.serviceCode,
    name: s.locationName,
    address: s.address ?? '',
    area: s.area ?? '',
    city: s.city ?? 'Chennai',
    pincode: s.pincode ?? '',
    serviceArea: s.serviceArea ?? '',
    latitude: s.latitude != null ? String(s.latitude) : '',
    longitude: s.longitude != null ? String(s.longitude) : '',
  }
}

function randomCode(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 8).toUpperCase()}`
}

/** 6-decimal string match -- good enough to tell "this is the point I just
 * pushed echoing back" from "this is a genuinely new drag on the map". */
function samePoint(
  a: { latitude: number; longitude: number } | null,
  lat: string,
  lng: string,
): boolean {
  if (!a || !lat || !lng) return false
  return a.latitude.toFixed(6) === Number(lat).toFixed(6) &&
    a.longitude.toFixed(6) === Number(lng).toFixed(6)
}

interface Props {
  kind: Kind
  /** null = a brand-new, not-yet-saved draft card. */
  initial: Location | null
  canWrite: boolean
  /** Whether this card currently owns the shared map's editor marker. */
  isMapActive: boolean
  /** A drag on the shared map's marker, fed back in -- only meaningful while isMapActive. */
  externalDragPoint: { latitude: number; longitude: number } | null
  /** This card has a point to show (or an updated one) -- claim the shared map marker. */
  onActivateWithPoint: (lat: number, lng: number) => void
  /** Done editing (saved, discarded, or cancelled) -- release the shared map marker. */
  onDeactivate: () => void
  onSaved: (saved: Location) => void
  onDeleted: (id: string) => void
  onDiscardDraft: () => void
}

export function LocationEditorCard({
  kind,
  initial,
  canWrite,
  isMapActive,
  externalDragPoint,
  onActivateWithPoint,
  onDeactivate,
  onSaved,
  onDeleted,
  onDiscardDraft,
}: Props) {
  const isNew = initial == null
  const [current, setCurrent] = useState<Location | null>(initial)
  const [form, setForm] = useState<FormState>(() =>
    initial ? toForm(kind, initial) : { ...EMPTY, code: randomCode(kind === 'warehouse' ? 'WH' : 'SRV') },
  )
  const [editing, setEditing] = useState(isNew)
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [geocoding, setGeocoding] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const sessionTokenRef = useRef(crypto.randomUUID())
  const debounceRef = useRef<number | undefined>(undefined)
  const geocodeDebounceRef = useRef<number | undefined>(undefined)
  const lastGeocodeAttemptRef = useRef('')
  const lastPushedPointRef = useRef<{ latitude: number; longitude: number } | null>(null)

  // Entering edit mode on an already-placed record shows its current pin on
  // the shared map immediately, draggable to correct -- not just after the
  // address is retyped.
  useEffect(() => {
    if (editing && form.latitude && form.longitude) {
      const lat = Number(form.latitude)
      const lng = Number(form.longitude)
      lastPushedPointRef.current = { latitude: lat, longitude: lng }
      onActivateWithPoint(lat, lng)
    }
    // Only on the editing:false -> true transition, not on every form change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing])

  // A drag on the shared map's marker, fed back in from the parent. Ignore
  // it if it's just an echo of the point this card itself just pushed.
  useEffect(() => {
    if (!isMapActive || !externalDragPoint) return
    if (samePoint(externalDragPoint, form.latitude, form.longitude)) return
    const { latitude, longitude } = externalDragPoint
    setForm((previous) => ({
      ...previous,
      latitude: latitude.toFixed(6),
      longitude: longitude.toFixed(6),
    }))
    lastPushedPointRef.current = externalDragPoint
    void (async () => {
      try {
        const reverse = await api.reverseGeocode(latitude, longitude)
        setForm((previous) => ({ ...previous, address: reverse.formattedAddress }))
      } catch {
        // The pin is still placed correctly even if reverse geocoding fails.
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalDragPoint, isMapActive])

  const pushPoint = (lat: number, lng: number) => {
    lastPushedPointRef.current = { latitude: lat, longitude: lng }
    onActivateWithPoint(lat, lng)
  }

  const resolveAddress = async (address: string) => {
    if (!address.trim()) return
    lastGeocodeAttemptRef.current = address.trim()
    setGeocoding(true)
    setNotice(null)
    try {
      const result = await api.geocode(address)
      setForm((previous) => ({
        ...previous,
        latitude: result.latitude.toFixed(6),
        longitude: result.longitude.toFixed(6),
      }))
      pushPoint(result.latitude, result.longitude)
      if (result.needsVerification) {
        setNotice(
          'This address matched only approximately. Drag the marker on the map to ' +
            'the exact location before saving.',
        )
      }
    } catch {
      setNotice(
        'Unable to locate this address. Please check the address or place the ' +
          'marker manually on the map.',
      )
    } finally {
      setGeocoding(false)
    }
  }

  const onAddressChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = event.target.value
    setForm((previous) => ({ ...previous, address: value }))
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
    const value = form.address.trim()
    if (value.length >= 3 && value !== lastGeocodeAttemptRef.current) {
      window.clearTimeout(geocodeDebounceRef.current)
      void resolveAddress(value)
    }
  }

  const chooseSuggestion = async (suggestion: AddressSuggestion) => {
    setForm((previous) => ({ ...previous, address: suggestion.description }))
    setShowSuggestions(false)
    setSuggestions([])
    sessionTokenRef.current = crypto.randomUUID()
    await resolveAddress(suggestion.description)
  }

  const save = async () => {
    setError(null)
    if (!form.code.trim() || !form.name.trim()) {
      setError('Code and name are required.')
      return
    }
    const latitude = form.latitude ? Number(form.latitude) : undefined
    const longitude = form.longitude ? Number(form.longitude) : undefined
    if (isNew && (latitude === undefined || longitude === undefined)) {
      setError('Search an address or place the marker on the map before saving.')
      return
    }

    setSaving(true)
    try {
      let saved: Location
      if (kind === 'warehouse') {
        saved =
          isNew || !current
            ? await api.createWarehouse({
                warehouseCode: form.code.trim(),
                warehouseName: form.name.trim(),
                address: form.address.trim() || undefined,
                city: form.city.trim() || undefined,
                pincode: form.pincode.trim() || undefined,
                latitude: latitude as number,
                longitude: longitude as number,
              })
            : await api.updateWarehouse(current.id, {
                warehouseName: form.name.trim(),
                address: form.address.trim() || undefined,
                city: form.city.trim() || undefined,
                pincode: form.pincode.trim() || undefined,
                latitude,
                longitude,
              })
      } else {
        saved =
          isNew || !current
            ? await api.createServiceLocation({
                serviceCode: form.code.trim(),
                locationName: form.name.trim(),
                address: form.address.trim() || undefined,
                area: form.area.trim() || undefined,
                city: form.city.trim() || undefined,
                pincode: form.pincode.trim() || undefined,
                serviceArea: form.serviceArea.trim() || undefined,
                latitude,
                longitude,
              })
            : await api.updateServiceLocation(current.id, {
                locationName: form.name.trim(),
                address: form.address.trim() || undefined,
                area: form.area.trim() || undefined,
                pincode: form.pincode.trim() || undefined,
                serviceArea: form.serviceArea.trim() || undefined,
                latitude,
                longitude,
              })
      }
      setCurrent(saved)
      setForm(toForm(kind, saved))
      setEditing(false)
      setNotice(null)
      onSaved(saved)
      onDeactivate()
    } catch (exception) {
      setError(
        exception instanceof ApiError ? exception.message : (exception as Error).message,
      )
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (isNew || !current) {
      onDeactivate()
      onDiscardDraft()
      return
    }
    if (!window.confirm(`Remove "${form.name || form.code}" from active service?`)) return
    setSaving(true)
    try {
      if (kind === 'warehouse') await api.deleteWarehouse(current.id)
      else await api.deleteServiceLocation(current.id)
      onDeactivate()
      onDeleted(current.id)
    } catch (exception) {
      setError(
        exception instanceof ApiError ? exception.message : (exception as Error).message,
      )
      setSaving(false)
    }
  }

  const fieldsDisabled = !canWrite || !editing || saving

  return (
    <div className="card" style={{ marginBottom: 12 }} data-testid={`${kind}-location-card`}>
      <div className="card__header">
        {form.name || (kind === 'warehouse' ? 'New warehouse' : 'New service location')}
      </div>
      <div className="card__body">
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

        <div className="field-row">
          <div className="field">
            <label>
              {kind === 'warehouse' ? 'Warehouse code' : 'Service code'}{' '}
              <span className="required">*</span>
            </label>
            <input
              value={form.code}
              onChange={(event) => setForm((p) => ({ ...p, code: event.target.value }))}
              disabled={fieldsDisabled || !isNew}
            />
          </div>
          <div className="field">
            <label>
              {kind === 'warehouse' ? 'Warehouse name' : 'Location name'}{' '}
              <span className="required">*</span>
            </label>
            <input
              value={form.name}
              onChange={(event) => setForm((p) => ({ ...p, name: event.target.value }))}
              disabled={fieldsDisabled}
            />
          </div>
        </div>

        <div className="field" style={{ position: 'relative' }}>
          <label>Address</label>
          <textarea
            value={form.address}
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

        <div className="field-row">
          <div className="field">
            <label>City</label>
            <input
              value={form.city}
              onChange={(event) => setForm((p) => ({ ...p, city: event.target.value }))}
              disabled={fieldsDisabled}
            />
          </div>
          <div className="field">
            <label>Pincode</label>
            <input
              value={form.pincode}
              onChange={(event) => setForm((p) => ({ ...p, pincode: event.target.value }))}
              inputMode="numeric"
              maxLength={6}
              disabled={fieldsDisabled}
            />
          </div>
        </div>

        {kind === 'service' && (
          <div className="field-row">
            <div className="field">
              <label>Area</label>
              <input
                value={form.area}
                onChange={(event) => setForm((p) => ({ ...p, area: event.target.value }))}
                disabled={fieldsDisabled}
              />
            </div>
            <div className="field">
              <label>Service area</label>
              <input
                value={form.serviceArea}
                onChange={(event) => setForm((p) => ({ ...p, serviceArea: event.target.value }))}
                disabled={fieldsDisabled}
              />
            </div>
          </div>
        )}

        <button
          type="button"
          className="btn btn--secondary btn--block"
          onClick={() => resolveAddress(form.address)}
          disabled={fieldsDisabled || geocoding || form.address.trim().length < 3}
          style={{ marginBottom: 13 }}
        >
          {geocoding ? <span className="spinner" aria-hidden="true" /> : '⌖'} Search location
        </button>

        <p className="subtle" style={{ margin: '0 0 12px' }}>
          {form.latitude && form.longitude
            ? `${form.latitude}, ${form.longitude}${editing ? ' — see the marker on the map above' : ''}`
            : 'No location pinned yet — search an address above.'}
        </p>

        <div className="row" style={{ marginTop: 13 }}>
          {canWrite && editing && (
            <button type="button" className="btn" onClick={save} disabled={saving}>
              {saving && <span className="spinner" aria-hidden="true" />} Save
            </button>
          )}
          {canWrite && !editing && (
            <button type="button" className="btn btn--secondary" onClick={() => setEditing(true)}>
              Edit
            </button>
          )}
          {canWrite && editing && !isNew && current && (
            <button
              type="button"
              className="btn btn--secondary"
              onClick={() => {
                setEditing(false)
                setForm(toForm(kind, current))
                setError(null)
                setNotice(null)
                onDeactivate()
              }}
            >
              Cancel
            </button>
          )}
          {canWrite && (
            <button type="button" className="btn btn--secondary" onClick={remove} disabled={saving}>
              {isNew ? 'Discard' : 'Delete'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
