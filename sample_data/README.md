# Import file format

`service_locations_template.csv` is a working example. Upload it through
**Administration → Import**, or:

```bash
curl -X POST https://<host>/api/v1/imports \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@sample_data/service_locations_template.csv"
```

## Columns

| Canonical name | Required | Notes |
|---|---|---|
| `service_code` | **yes** | Unique. Also accepted as *Customer ID*, `code`, `id`, `customer_code` |
| `location_name` | **yes** | Also accepted as *Customer Name*, `name` |
| `address` | conditional | Required only when latitude/longitude are blank — it is what gets geocoded |
| `area` | no | Improves geocoding accuracy noticeably in Chennai |
| `city` | no | Defaults to Chennai |
| `pincode` | no | Improves geocoding accuracy |
| `latitude` / `longitude` | no | Supply both or neither. Supplied coordinates always win over geocoding |
| `service_area` | no | Used for grouping and reporting |
| `route` | no | Route code. Created automatically if it does not exist |
| `status` | no | `ACTIVE` (default) or `INACTIVE`. Only ACTIVE rows define coverage |

Header matching is forgiving: `Customer ID`, `customer_id`, `CustomerID` and
`customer-id` all resolve to `service_code`. The full alias list is in
`backend/app/services/import_service.py`.

`.xlsx` files work the same way — the first sheet is read.

## What happens on upload

Uploading **validates only**. Nothing is written to `service_locations` until
you explicitly commit. The validation report tells you, per row:

- rows with a missing code or name → `INVALID`
- codes that already exist, or repeat within the file → `DUPLICATE`
- coordinates out of range, or only one of the pair → `INVALID`
- rows without coordinates → geocoded from the address; a failure is
  `GEOCODE_FAILED`, never a silent guess
- coordinates outside the Chennai bounding box → **warning**, still `VALID`
  (expanding beyond Chennai is an expected future change, so this is not
  treated as an error)

Download it as a spreadsheet and review it with the operations team:

```bash
curl -o report.xlsx "https://<host>/api/v1/imports/<batch-id>/report?fmt=xlsx" \
  -H "Authorization: Bearer $TOKEN"
```

Then commit — only `VALID` rows are created:

```bash
curl -X POST "https://<host>/api/v1/imports/<batch-id>/commit" \
  -H "Authorization: Bearer $TOKEN"
```

## Why the two steps matter

The contents of `service_locations` *are* the coverage definition. A location
that is silently dropped does not produce an error message — it produces a
customer who is wrongly told SERVICE NOT AVAILABLE, weeks later, with nothing
in the logs to explain it. Reviewing the report before committing is the only
point at which that is cheap to catch.

## Row in the example with no coordinates

`C106` deliberately has blank latitude and longitude so you can see the
geocoding path in the validation report.
