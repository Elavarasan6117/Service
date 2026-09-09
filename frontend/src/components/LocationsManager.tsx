import { useEffect, useState } from 'react'

import { api } from '../api/client'
import type { ServiceLocation, Warehouse } from '../types/api'
import { LocationEditorCard } from './LocationEditorCard'

type Kind = 'warehouse' | 'service'
export type EditorPoint = { latitude: number; longitude: number; kind: Kind } | null

interface Props {
  canManageWarehouses: boolean
  canManageServiceLocations: boolean
  /**
   * Called after a warehouse or service location is created, edited,
   * deleted, or reactivated, so the caller can refresh anything else that
   * reads this data -- the Live Operations Map's own warehouse/service-
   * location layers in particular, which only show ACTIVE, already-saved
   * records and have no other way to learn about the change.
   */
  onLocationsChanged?: () => void
  /** The shared map's editor marker position, owned by the parent (it also
   * feeds OperationsMap directly) so a drag on the map can be routed back to
   * whichever card is currently active. */
  editorPoint: EditorPoint
  onEditorPointChange: (point: EditorPoint) => void
}

interface Entry<T> {
  /** Stable across saves: the record's id once saved, a temp id before that. */
  key: string
  saved: T | null
}

/**
 * Manages an unlimited, dynamically-sized list of records -- no count is
 * ever hard-coded. "+ Add" appends a blank draft card; it only becomes a real
 * record (and gets a real `key`) once its own Save succeeds.
 *
 * Exactly one card at a time "owns" the shared map's editor marker (tracked
 * here as `activeCard`) -- whichever one most recently produced a point
 * (typed/searched an address, or was just opened for editing).
 */
export function LocationsManager({
  canManageWarehouses,
  canManageServiceLocations,
  onLocationsChanged,
  editorPoint,
  onEditorPointChange,
}: Props) {
  const [warehouses, setWarehouses] = useState<Entry<Warehouse>[]>([])
  const [serviceLocations, setServiceLocations] = useState<Entry<ServiceLocation>[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeCard, setActiveCard] = useState<{ listKind: Kind; key: string } | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [warehouseList, serviceLocationPage] = await Promise.all([
        api.listWarehouses(),
        api.listServiceLocations({ page_size: 200 }),
      ])
      // A deleted location is gone for good from this page -- there is no
      // "Reactivate" here, so an INACTIVE record has nothing left to show.
      setWarehouses(
        warehouseList
          .filter((item) => item.status === 'ACTIVE')
          .map((item) => ({ key: item.id, saved: item })),
      )
      setServiceLocations(
        serviceLocationPage.items
          .filter((item) => item.status === 'ACTIVE')
          .map((item) => ({ key: item.id, saved: item })),
      )
    } catch (exception) {
      setError((exception as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const isActive = (listKind: Kind, key: string) =>
    activeCard?.listKind === listKind && activeCard.key === key

  return (
    <div className="card">
      <div className="card__header">Locations</div>
      <div className="card__body">
        {error && (
          <div className="alert alert--error" role="alert">
            <span aria-hidden="true">⚠</span>
            <div className="alert__body">{error}</div>
          </div>
        )}
        {loading ? (
          <div className="empty">Loading locations…</div>
        ) : (
          <>
            <div className="result-item__label" style={{ marginBottom: 8 }}>
              Warehouse Locations
            </div>
            {warehouses.length === 0 && (
              <div className="empty" style={{ marginBottom: 12 }}>
                No warehouse locations yet.
              </div>
            )}
            {warehouses.map((entry) => (
              <LocationEditorCard
                key={entry.key}
                kind="warehouse"
                initial={entry.saved}
                canWrite={canManageWarehouses}
                isMapActive={isActive('warehouse', entry.key)}
                externalDragPoint={
                  isActive('warehouse', entry.key) && editorPoint
                    ? { latitude: editorPoint.latitude, longitude: editorPoint.longitude }
                    : null
                }
                onActivateWithPoint={(lat, lng) => {
                  setActiveCard({ listKind: 'warehouse', key: entry.key })
                  onEditorPointChange({ latitude: lat, longitude: lng, kind: 'warehouse' })
                }}
                onDeactivate={() => {
                  if (isActive('warehouse', entry.key)) {
                    setActiveCard(null)
                    onEditorPointChange(null)
                  }
                }}
                onSaved={(saved) => {
                  setWarehouses((previous) =>
                    previous.map((e) =>
                      e.key === entry.key ? { key: saved.id, saved: saved as Warehouse } : e,
                    ),
                  )
                  onLocationsChanged?.()
                }}
                onDeleted={() => {
                  setWarehouses((previous) => previous.filter((e) => e.key !== entry.key))
                  onLocationsChanged?.()
                }}
                onDiscardDraft={() =>
                  setWarehouses((previous) => previous.filter((e) => e.key !== entry.key))
                }
              />
            ))}
            {canManageWarehouses && (
              <button
                type="button"
                className="btn btn--secondary btn--block"
                onClick={() =>
                  setWarehouses((previous) => [
                    ...previous,
                    { key: crypto.randomUUID(), saved: null },
                  ])
                }
                style={{ marginBottom: 24 }}
              >
                + Add Warehouse Location
              </button>
            )}

            <div className="result-item__label" style={{ margin: '4px 0 8px' }}>
              Service Locations
            </div>
            {serviceLocations.length === 0 && (
              <div className="empty" style={{ marginBottom: 12 }}>
                No service locations yet.
              </div>
            )}
            {serviceLocations.map((entry) => (
              <LocationEditorCard
                key={entry.key}
                kind="service"
                initial={entry.saved}
                canWrite={canManageServiceLocations}
                isMapActive={isActive('service', entry.key)}
                externalDragPoint={
                  isActive('service', entry.key) && editorPoint
                    ? { latitude: editorPoint.latitude, longitude: editorPoint.longitude }
                    : null
                }
                onActivateWithPoint={(lat, lng) => {
                  setActiveCard({ listKind: 'service', key: entry.key })
                  onEditorPointChange({ latitude: lat, longitude: lng, kind: 'service' })
                }}
                onDeactivate={() => {
                  if (isActive('service', entry.key)) {
                    setActiveCard(null)
                    onEditorPointChange(null)
                  }
                }}
                onSaved={(saved) => {
                  setServiceLocations((previous) =>
                    previous.map((e) =>
                      e.key === entry.key
                        ? { key: saved.id, saved: saved as ServiceLocation }
                        : e,
                    ),
                  )
                  onLocationsChanged?.()
                }}
                onDeleted={() => {
                  setServiceLocations((previous) => previous.filter((e) => e.key !== entry.key))
                  onLocationsChanged?.()
                }}
                onDiscardDraft={() =>
                  setServiceLocations((previous) => previous.filter((e) => e.key !== entry.key))
                }
              />
            ))}
            {canManageServiceLocations && (
              <button
                type="button"
                className="btn btn--secondary btn--block"
                onClick={() =>
                  setServiceLocations((previous) => [
                    ...previous,
                    { key: crypto.randomUUID(), saved: null },
                  ])
                }
              >
                + Add Service Location
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )
}
