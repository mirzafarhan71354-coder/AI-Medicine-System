# Medicine catalog data

The analyzer searches two sources:

- `data/medicine_catalog.sqlite3` is a locally packaged snapshot of the FDA
  National Drug Code (NDC) Directory. It contains human prescription and OTC
  product listings whose NDC listing-expiration date is on or after the
  snapshot date and whose marketing start date is not in the future. Separate
  product records are deduplicated by listed product details; brands,
  manufacturers, strengths, and formulations are retained when they differ.
  Product names, generic names, active ingredients, strengths, dosage forms,
  labelers, routes, and available pharmacologic classes are taken from the
  source record. The checked-in snapshot currently has 95,347 distinct
  product records dated 2026-10-26.
- `medicine_dataset.py` retains the project's pre-existing hand-entered
  references. They have no recorded source verification and are labeled as
  unverified in search results.

The snapshot is generated from the public
[OpenFDA NDC API](https://api.fda.gov/drug/ndc.json). Refresh it from the
repository root with:

```powershell
python scripts\refresh_medicine_catalog.py
```

The refresh needs network access and the existing `requests` dependency. It
replaces the packaged snapshot only after a complete successful download.
The API's [NDC data documentation](https://open.fda.gov/apis/drug/ndc/)
explains that NDC listing is not an FDA approval or endorsement.

## Coverage and limitations

This is **not** a complete global medicine database and is **not** an
India-specific brand register. The FDA NDC Directory describes US product
listings; a listing does not prove that a product is currently marketed,
available in India, or suitable for a particular patient. Indian brand names
and spellings not present in the source will not be found unless already
present in the project's legacy list.

The NDC records do not consistently provide indications, patient dosing,
warnings, or verified alternate spellings. For selected matches, the analyzer
tries to retrieve indications, dosage instructions, and warnings from the
corresponding public FDA drug-label endpoint. If a label field is missing or
the service is unavailable, the UI says so rather than filling it by guess.
The recorded source aliases are searchable names, not therapeutic-equivalence
claims.

This catalog is for name lookup and reference only. It does not provide
prescribing advice, interaction validation, or a substitute for a pharmacist
or clinician.
