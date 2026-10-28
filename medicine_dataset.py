import json
import re
import sqlite3
from pathlib import Path


medicine_data = [

# 🔴 PAINKILLERS / FEVER (25)
{"name": "PARACETAMOL", "usage": "Fever, Pain relief", "dosage": "500-650 mg", "type": "Tablet", "side_effects": "Rare, liver damage (overdose)", "category": "Painkiller"},
{"name": "DOLO 650", "usage": "Fever, Body pain", "dosage": "650 mg", "type": "Tablet", "side_effects": "Nausea", "category": "Painkiller"},
{"name": "CROCIN", "usage": "Fever", "dosage": "500 mg", "type": "Tablet", "side_effects": "Rare", "category": "Painkiller"},
{"name": "CALPOL 500", "usage": "Fever", "dosage": "500 mg", "type": "Tablet", "side_effects": "Rare", "category": "Painkiller"},
{"name": "CALPOL 650", "usage": "Fever", "dosage": "650 mg", "type": "Tablet", "side_effects": "Rare", "category": "Painkiller"},
{"name": "DISPRIN", "usage": "Headache", "dosage": "As needed", "type": "Tablet", "side_effects": "Acidity", "category": "Painkiller"},
{"name": "IBUGESIC", "usage": "Pain", "dosage": "400 mg", "type": "Tablet", "side_effects": "Stomach irritation", "category": "Painkiller"},
{"name": "NAPROXEN", "usage": "Pain", "dosage": "250-500 mg", "type": "Tablet", "side_effects": "Stomach pain", "category": "Painkiller"},
{"name": "DICLOFENAC", "usage": "Pain", "dosage": "50 mg", "type": "Tablet", "side_effects": "Stomach pain", "category": "Painkiller"},
{"name": "ETORICOXIB", "usage": "Joint pain", "dosage": "60 mg", "type": "Tablet", "side_effects": "Dizziness", "category": "Painkiller"},
{"name": "TRAMADOL", "usage": "Severe pain", "dosage": "50 mg", "type": "Tablet", "side_effects": "Nausea", "category": "Painkiller"},
{"name": "ACELOFENAC", "usage": "Pain", "dosage": "100 mg", "type": "Tablet", "side_effects": "Nausea", "category": "Painkiller"},
{"name": "MEFENAMIC ACID", "usage": "Pain", "dosage": "250 mg", "type": "Tablet", "side_effects": "Diarrhea", "category": "Painkiller"},
{"name": "KETOROLAC", "usage": "Severe pain", "dosage": "10 mg", "type": "Tablet", "side_effects": "Stomach pain", "category": "Painkiller"},
{"name": "PIROXICAM", "usage": "Inflammation", "dosage": "20 mg", "type": "Tablet", "side_effects": "Dizziness", "category": "Painkiller"},
{"name": "LORNoxicam", "usage": "Pain", "dosage": "8 mg", "type": "Tablet", "side_effects": "Nausea", "category": "Painkiller"},
{"name": "NIMESULIDE", "usage": "Pain", "dosage": "100 mg", "type": "Tablet", "side_effects": "Liver risk", "category": "Painkiller"},
{"name": "DICLOMOL", "usage": "Pain", "dosage": "As prescribed", "type": "Tablet", "side_effects": "Nausea", "category": "Painkiller"},
{"name": "FLEXON", "usage": "Pain", "dosage": "As prescribed", "type": "Tablet", "side_effects": "Acidity", "category": "Painkiller"},
{"name": "ZERODOL", "usage": "Pain", "dosage": "As prescribed", "type": "Tablet", "side_effects": "Stomach pain", "category": "Painkiller"},
{"name": "ZERODOL SP", "usage": "Pain", "dosage": "As prescribed", "type": "Tablet", "side_effects": "Dizziness", "category": "Painkiller"},
{"name": "ZERODOL P", "usage": "Pain", "dosage": "As prescribed", "type": "Tablet", "side_effects": "Nausea", "category": "Painkiller"},
{"name": "DOLONEX", "usage": "Pain", "dosage": "20 mg", "type": "Tablet", "side_effects": "Dizziness", "category": "Painkiller"},
{"name": "ULTRACET", "usage": "Pain", "dosage": "As prescribed", "type": "Tablet", "side_effects": "Sleepiness", "category": "Painkiller"},
{"name": "MYOSPaz", "usage": "Muscle pain", "dosage": "As prescribed", "type": "Tablet", "side_effects": "Drowsiness", "category": "Painkiller"},

# 🔵 ANTIBIOTICS (40)
{"name": "AMOXICILLIN", "usage": "Infection", "dosage": "500 mg", "type": "Capsule", "side_effects": "Rash", "category": "Antibiotic"},
{"name": "AUGMENTIN 625", "usage": "Infection", "dosage": "625 mg", "type": "Tablet", "side_effects": "Diarrhea", "category": "Antibiotic"},
{"name": "AZITHROMYCIN 500", "usage": "Infection", "dosage": "500 mg", "type": "Tablet", "side_effects": "Diarrhea", "category": "Antibiotic"},
{"name": "CIPROFLOXACIN", "usage": "Infection", "dosage": "500 mg", "type": "Tablet", "side_effects": "Nausea", "category": "Antibiotic"},
{"name": "OFLOXACIN", "usage": "Infection", "dosage": "200 mg", "type": "Tablet", "side_effects": "Dizziness", "category": "Antibiotic"},
{"name": "LEVOFLOXACIN", "usage": "Infection", "dosage": "500 mg", "type": "Tablet", "side_effects": "Nausea", "category": "Antibiotic"},
{"name": "DOXYCYCLINE", "usage": "Infection", "dosage": "100 mg", "type": "Capsule", "side_effects": "Sun sensitivity", "category": "Antibiotic"},
{"name": "CEFIXIME", "usage": "Infection", "dosage": "200 mg", "type": "Tablet", "side_effects": "Diarrhea", "category": "Antibiotic"},
{"name": "RIFAXIN 550", "usage": "Diarrhea, IBS", "dosage": "550 mg", "type": "Tablet", "side_effects": "Nausea", "category": "Antibiotic"},
{"name": "METRONIDAZOLE", "usage": "Infection", "dosage": "400 mg", "type": "Tablet", "side_effects": "Metallic taste", "category": "Antibiotic"},
{"name": "TINIDAZOLE", "usage": "Infection", "dosage": "500 mg", "type": "Tablet", "side_effects": "Nausea", "category": "Antibiotic"},
{"name": "CLINDAMYCIN", "usage": "Infection", "dosage": "300 mg", "type": "Capsule", "side_effects": "Diarrhea", "category": "Antibiotic"},
{"name": "LINEZOLID", "usage": "Infection", "dosage": "600 mg", "type": "Tablet", "side_effects": "Headache", "category": "Antibiotic"},
{"name": "CEFTRIAXONE", "usage": "Infection", "dosage": "Injection", "type": "Injection", "side_effects": "Pain at site", "category": "Antibiotic"},
{"name": "MEROPENEM", "usage": "Severe infection", "dosage": "Injection", "type": "Injection", "side_effects": "Nausea", "category": "Antibiotic"},
{"name": "IMIPENEM", "usage": "Severe infection", "dosage": "Injection", "type": "Injection", "side_effects": "Seizure risk", "category": "Antibiotic"},

# 🟢 DIABETES (25)
{"name": "METFORMIN", "usage": "Diabetes", "dosage": "500 mg", "type": "Tablet", "side_effects": "Diarrhea", "category": "Antidiabetic"},
{"name": "GLIMDA MV2", "usage": "Type 2 Diabetes", "dosage": "As prescribed", "type": "Tablet", "side_effects": "Low sugar", "category": "Antidiabetic"},
{"name": "AGIVILDA M 50/500", "usage": "Type 2 Diabetes", "dosage": "As prescribed", "type": "Tablet", "side_effects": "Nausea", "category": "Antidiabetic"},
{"name": "GLIMEPIRIDE", "usage": "Diabetes", "dosage": "1 mg", "type": "Tablet", "side_effects": "Hypoglycemia", "category": "Antidiabetic"},
{"name": "GLICLAZIDE", "usage": "Diabetes", "dosage": "80 mg", "type": "Tablet", "side_effects": "Low sugar", "category": "Antidiabetic"},
{"name": "SITAGLIPTIN", "usage": "Diabetes", "dosage": "100 mg", "type": "Tablet", "side_effects": "Headache", "category": "Antidiabetic"},
{"name": "VILDAGLIPTIN", "usage": "Diabetes", "dosage": "50 mg", "type": "Tablet", "side_effects": "Nausea", "category": "Antidiabetic"},
{"name": "INSULIN", "usage": "Diabetes", "dosage": "Injection", "type": "Injection", "side_effects": "Low sugar", "category": "Antidiabetic"},

# ❤️ BP / HEART (25)
{"name": "TEMSVAN 40", "usage": "High BP", "dosage": "40 mg", "type": "Tablet", "side_effects": "Dizziness", "category": "Antihypertensive"},
{"name": "TEMSVAN 20", "usage": "High BP", "dosage": "20 mg", "type": "Tablet", "side_effects": "Dizziness", "category": "Antihypertensive"},
{"name": "TELMISARTAN", "usage": "High BP", "dosage": "40 mg", "type": "Tablet", "side_effects": "Low BP", "category": "Antihypertensive"},
{"name": "AMLODIPINE", "usage": "High BP", "dosage": "5 mg", "type": "Tablet", "side_effects": "Swelling", "category": "Antihypertensive"},
{"name": "LOSARTAN", "usage": "High BP", "dosage": "50 mg", "type": "Tablet", "side_effects": "Dizziness", "category": "Antihypertensive"},
{"name": "ATENOLOL", "usage": "BP", "dosage": "50 mg", "type": "Tablet", "side_effects": "Fatigue", "category": "Antihypertensive"},
{"name": "PROPRANOLOL", "usage": "BP", "dosage": "40 mg", "type": "Tablet", "side_effects": "Bradycardia", "category": "Antihypertensive"},

# 🟡 ACIDITY / GUT (20)
{"name": "PANTOPRAZOLE", "usage": "Acidity", "dosage": "40 mg", "type": "Tablet", "side_effects": "Headache", "category": "Antacid"},
{"name": "OMEPRAZOLE", "usage": "Acidity", "dosage": "20 mg", "type": "Capsule", "side_effects": "Headache", "category": "Antacid"},
{"name": "RABEPRAZOLE", "usage": "Acidity", "dosage": "20 mg", "type": "Tablet", "side_effects": "Nausea", "category": "Antacid"},
{"name": "VELGUT", "usage": "Gut health", "dosage": "As prescribed", "type": "Capsule", "side_effects": "Rare", "category": "Probiotic"},
{"name": "SPORLAC", "usage": "Probiotic", "dosage": "As prescribed", "type": "Capsule", "side_effects": "Gas", "category": "Probiotic"},
{"name": "ECONORM", "usage": "Gut health", "dosage": "As prescribed", "type": "Capsule", "side_effects": "Bloating", "category": "Probiotic"},

# 🧪 SUPPLEMENTS (15)
{"name": "ZINCOVIT", "usage": "Vitamin supplement", "dosage": "Daily", "type": "Tablet", "side_effects": "Rare", "category": "Supplement"},
{"name": "BECOSULES", "usage": "Vitamin deficiency", "dosage": "Daily", "type": "Capsule", "side_effects": "Rare", "category": "Supplement"},
{"name": "VITAMIN D3", "usage": "Bone health", "dosage": "Weekly", "type": "Capsule", "side_effects": "Rare", "category": "Supplement"},
{"name": "FERROUS SULFATE", "usage": "Iron deficiency", "dosage": "Daily", "type": "Tablet", "side_effects": "Constipation", "category": "Supplement"}

]

medicine_list = [m["name"] for m in medicine_data]

_CATALOG_PATH = Path(__file__).resolve().parent / "data" / "medicine_catalog.sqlite3"


def _normalize_name(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def search_medicines(query, limit=5):
    """Search the legacy references and the downloaded FDA NDC snapshot."""
    normalized = _normalize_name(query)
    if len(normalized) < 2:
        return []

    local_matches = []
    for medicine in medicine_data:
        searchable = " ".join(str(medicine.get(key, "")) for key in (
            "name", "generic_name", "brand_name", "active_ingredients",
            "aliases", "alternative_names"
        ))
        if normalized in _normalize_name(searchable):
            match = medicine.copy()
            match["source"] = "Legacy project data (unverified)"
            match["verification_status"] = (
                "Carried over from the project; no source verification is recorded."
            )
            match["_rank"] = (
                0 if normalized == _normalize_name(medicine.get("name"))
                else 1 if _normalize_name(medicine.get("name")).startswith(normalized)
                else 2
            )
            local_matches.append(match)

    catalog_matches = []
    if _CATALOG_PATH.is_file():
        pattern = f"%{normalized}%"
        raw_query = str(query).strip().casefold()
        with sqlite3.connect(_CATALOG_PATH) as connection:
            rows = connection.execute(
                """
                SELECT product_id, product_ndc, name, brand_name, brand_name_base,
                       generic_name, active_ingredients, strength, dosage_form,
                       manufacturer, category, product_type, route, aliases
                FROM medicines
                WHERE search_text LIKE ?
                ORDER BY
                    CASE
                        WHEN lower(name) = ? THEN 0
                        WHEN lower(brand_name) = ? THEN 1
                        WHEN lower(generic_name) = ? THEN 2
                        WHEN lower(name) LIKE ? THEN 3
                        WHEN lower(brand_name) LIKE ? THEN 4
                        WHEN lower(generic_name) LIKE ? THEN 5
                        ELSE 6
                    END,
                    name, strength, dosage_form
                LIMIT ?
                """,
                (
                    pattern, raw_query, raw_query, raw_query,
                    f"{raw_query}%", f"{raw_query}%", f"{raw_query}%",
                    limit,
                ),
            ).fetchall()

        for row in rows:
            (
                product_id, product_ndc, name, brand_name, brand_name_base,
                generic_name, active_ingredients, strength, dosage_form,
                manufacturer, category, product_type, route, aliases,
            ) = row
            catalog_matches.append({
                "name": name,
                "brand_name": brand_name,
                "brand_name_base": brand_name_base,
                "generic_name": generic_name,
                "active_ingredients": active_ingredients,
                "strength": strength,
                "type": dosage_form,
                "dosage_form": dosage_form,
                "manufacturer": manufacturer,
                "category": category or "Not listed in the NDC record",
                "usage": "Not provided in the FDA NDC Directory.",
                "dosage": "See the current product label; dosing instructions are not in the NDC Directory.",
                "side_effects": "Not provided in the FDA NDC Directory.",
                "product_type": product_type,
                "route": route,
                "product_ndc": product_ndc,
                "product_id": product_id,
                "aliases": json.loads(aliases or "[]"),
                "source": "FDA NDC Directory (US listing)",
                "verification_status": (
                    "Product listing fields are from the FDA NDC Directory; "
                    "this listing does not establish availability in India."
                ),
                "_rank": (
                    3 if normalized == _normalize_name(name)
                    else 4 if normalized == _normalize_name(brand_name)
                    else 5 if normalized == _normalize_name(generic_name)
                    else 6
                ),
            })

    matches = sorted(local_matches + catalog_matches, key=lambda item: item["_rank"])
    results = []
    seen = set()
    for match in matches:
        key = match.get("product_id") or f"legacy:{_normalize_name(match['name'])}"
        if key in seen:
            continue
        seen.add(key)
        match.pop("_rank", None)
        results.append(match)
        if len(results) == limit:
            break
    return results