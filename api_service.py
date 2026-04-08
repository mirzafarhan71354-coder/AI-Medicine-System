import requests

cache = {}

# 🔥 CLEAN FUNCTION (important)
def clean_text(text, max_length=200):
    if not text:
        return "Not available"

    text = text.replace("\n", " ").strip()
    return text[:max_length] + "..." if len(text) > max_length else text


def fetch_medicine_from_api(name):
    if name in cache:
        return cache[name]

    url = "https://api.fda.gov/drug/label.json"

    try:
        params = {
            "search": f"openfda.generic_name:{name}",
            "limit": 1
        }

        res = requests.get(url, params=params)

        if res.status_code == 200:
            data = res.json()

            if "results" in data:
                r = data["results"][0]

                result = {
                    "name": name,
                    "category": "General",  # 🔥 added for UI consistency
                    "uses": clean_text(r.get("purpose", ["Not available"])[0], 100),
                    "dosage": clean_text(r.get("dosage_and_administration", ["Not available"])[0], 150),
                    "side_effects": clean_text(r.get("warnings", ["Not available"])[0], 200),
                    "source": "API"
                }

                cache[name] = result
                return result

    except Exception as e:
        print("API Error:", e)

    return None