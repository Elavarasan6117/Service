/**
 * API contract types.
 *
 * These mirror the Pydantic schemas on the backend. The backend serves an
 * OpenAPI document at /api/v1/openapi.json; generating these from it is the
 * recommended next step once the contract settles.
 */

export type ServiceStatus =
  | 'PENDING'
  | 'CALCULATING'
  | 'AVAILABLE'
  | 'NOT_AVAILABLE'
  | 'LOCATION_VERIFICATION_REQUIRED'
  | 'ROUTE_CALCULATION_ERROR'
  | 'NO_SERVICE_LOCATION_CONFIGURED'
  | 'PENDING_REVIEW'

export type UserRole = 'ADMIN' | 'SUPERVISOR' | 'OPERATIONS' | 'SALES' | 'VIEW_ONLY'

export interface User {
  id: string
  username: string
  email: string
  fullName: string
  role: UserRole
  isActive: boolean
  permissions: string[]
}

export interface NearestServiceLocation {
  id: string
  serviceCode: string
  name: string
  latitude: number | null
  longitude: number | null
  serviceArea: string | null
  distanceMeters: number
  distanceKm: number
}

export interface RouteInfo {
  distanceMeters: number
  durationSeconds: number | null
  geometry: string | null
  geometryFormat: string | null
}

export interface ServiceabilityResult {
  customerId: string | null
  customerName: string | null
  checkId: string | null
  status: ServiceStatus
  thresholdMeters: number
  /** Always 'ROAD_DISTANCE'. The decision is never made from a straight line. */
  distanceType: string
  routingProvider: string | null
  nearestServiceLocation: NearestServiceLocation | null
  route: RouteInfo | null
  marginMeters: number | null
  candidateCount: number
  cacheHit: boolean
  degraded: boolean
  errorCode: string | null
  message: string | null
  reason: string | null
  retryable: boolean
  calculatedAt: string | null
  durationMs: number | null
}

export interface Customer {
  id: string
  customerCode: string
  customerName: string
  phone: string | null
  address: string
  formattedAddress: string | null
  area: string | null
  city: string
  pincode: string | null
  serviceType: string | null
  latitude: number | null
  longitude: number | null
  coordinateSource: string | null
  serviceStatus: ServiceStatus
  nearestServiceLocationId: string | null
  nearestServiceDistanceMeters: number | null
  createdAt: string
  updatedAt: string
}

export interface CustomerCreateResponse {
  customer: Customer
  serviceability: ServiceabilityResult | null
}

export interface ServiceLocation {
  id: string
  serviceCode: string
  locationName: string
  address: string | null
  area: string | null
  city: string | null
  pincode: string | null
  latitude: number | null
  longitude: number | null
  serviceArea: string | null
  status: 'ACTIVE' | 'INACTIVE' | 'PENDING_VERIFICATION' | 'INVALID'
  coordinateSource: string
}

export interface ServiceLocationCreatePayload {
  serviceCode: string
  locationName: string
  address?: string
  area?: string
  city?: string
  pincode?: string
  latitude?: number
  longitude?: number
  serviceArea?: string
}

export interface ServiceLocationUpdatePayload {
  locationName?: string
  address?: string
  area?: string
  pincode?: string
  latitude?: number
  longitude?: number
  serviceArea?: string
  status?: ServiceLocation['status']
}

export interface Warehouse {
  id: string
  warehouseCode: string
  warehouseName: string
  address: string | null
  city: string | null
  pincode: string | null
  latitude: number
  longitude: number
  status: string
}

export interface WarehouseCreatePayload {
  warehouseCode: string
  warehouseName: string
  address?: string
  city?: string
  pincode?: string
  latitude: number
  longitude: number
}

export interface WarehouseUpdatePayload {
  warehouseName?: string
  address?: string
  city?: string
  pincode?: string
  latitude?: number
  longitude?: number
  status?: Warehouse['status']
}

export interface MapCustomer {
  id: string
  customerCode: string
  customerName: string
  address: string
  latitude: number
  longitude: number
  serviceStatus: ServiceStatus
  nearestServiceLocationId: string | null
  nearestServiceDistanceMeters: number | null
}

export interface MapOperations {
  thresholdMeters: number
  center: { latitude: number; longitude: number }
  zoom: number
  tileUrl: string
  tileAttribution: string
  warehouses: Warehouse[]
  serviceLocations: ServiceLocation[]
  customers: MapCustomer[]
  generatedAt: string
}

export interface DashboardMetrics {
  date: string
  newCustomers: number
  serviceAvailable: number
  serviceNotAvailable: number
  locationVerificationRequired: number
  routeCalculationErrors: number
  pendingReview: number
  averageDistanceMeters: number | null
  medianDistanceMeters: number | null
  thresholdMeters: number
  byArea: { area: string; count: number }[]
  totalChecks: number
  cacheHitRate: number | null
}

export interface ServiceabilityCheck {
  id: string
  customerId: string | null
  customerCode: string | null
  customerName: string | null
  nearestServiceCode: string | null
  calculatedDistanceMeters: number | null
  distanceKm: number | null
  distanceType: string
  routingProvider: string | null
  thresholdMeters: number
  result: string
  candidateCount: number
  cacheHit: boolean
  errorCode: string | null
  createdByLabel: string
  createdAt: string
}

export interface AddressSuggestion {
  description: string
  placeId: string
  mainText: string
  secondaryText: string
}

export interface AppConfigItem {
  key: string
  value: string
  valueType: string
  description: string | null
  requiresAdmin: boolean
  minValue: string | null
  maxValue: string | null
  updatedAt: string
}

export interface ConfigAuditItem {
  id: string
  configKey: string
  oldValue: string | null
  newValue: string
  changedByLabel: string
  reason: string
  createdAt: string
}

export interface Page<T> {
  items: T[]
  meta: { total: number; page: number; pageSize: number; totalPages: number }
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details: { field?: string; issue?: string; message?: string }[]
    requestId: string | null
  }
}
