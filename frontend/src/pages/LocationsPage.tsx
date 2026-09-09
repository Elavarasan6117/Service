import { useCallback, useEffect, useState } from 'react'

import { api } from '../api/client'
import type { EditorPoint } from '../components/LocationsManager'
import { LocationsManager } from '../components/LocationsManager'
import { OperationsMap } from '../components/OperationsMap'
import type { MapOperations } from '../types/api'

interface Props {
  canManageWarehouses: boolean
  canManageServiceLocations: boolean
}

export function LocationsPage({ canManageWarehouses, canManageServiceLocations }: Props) {
  const [mapData, setMapData] = useState<MapOperations | null>(null)
  const [editorPoint, setEditorPoint] = useState<EditorPoint>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setMapData(await api.mapOperations({ customer_limit: 1 }))
      setError(null)
    } catch (exception) {
      setError((exception as Error).message)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  return (
    <div className="layout">
      <div className="column">
        <div className="card">
          <div className="card__header">
            Locations map
            <span className="subtle" style={{ textTransform: 'none' }}>
              {mapData
                ? `${mapData.warehouses.length} warehouses · ${mapData.serviceLocations.length} service locations`
                : 'loading…'}
            </span>
          </div>
          <div className="card__body card__body--flush">
            {error && (
              <div className="alert alert--error" role="alert">
                <span aria-hidden="true">⚠</span>
                <div className="alert__body">{error}</div>
              </div>
            )}
            <OperationsMap
              data={mapData}
              result={null}
              draftPoint={null}
              draggable={false}
              editorPoints={
                editorPoint
                  ? [
                      {
                        id: 'active',
                        kind: editorPoint.kind,
                        latitude: editorPoint.latitude,
                        longitude: editorPoint.longitude,
                      },
                    ]
                  : []
              }
              onEditorDragEnd={(_id, latitude, longitude) =>
                setEditorPoint((previous) =>
                  previous ? { ...previous, latitude, longitude } : previous,
                )
              }
            />
          </div>
        </div>
      </div>

      <div className="column">
        <LocationsManager
          canManageWarehouses={canManageWarehouses}
          canManageServiceLocations={canManageServiceLocations}
          onLocationsChanged={refresh}
          editorPoint={editorPoint}
          onEditorPointChange={setEditorPoint}
        />
      </div>
    </div>
  )
}
