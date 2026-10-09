/** Add known locality details to a free-form address without duplicating text. */
export function addressWithContext(address: string, ...context: (string | null | undefined)[]): string {
  const primary = address.trim()
  const normalized = primary.toLocaleLowerCase()
  const additions = context
    .map((part) => part?.trim())
    .filter((part): part is string => Boolean(part) && !normalized.includes(part!.toLocaleLowerCase()))
  return [primary, ...additions].join(', ')
}
