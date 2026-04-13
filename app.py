from flask import Flask, render_template, request
import pytesseract
from PIL import Image
import re
import requests
from difflib import get_close_matches
from medicine_dataset import medicine_list, medicine_data
from drug_interactions import drug_interactions   # ✅ NEW

app = Flask(__name__)

# 🔥 API CACHE
api_cache = {}

# 🔥 CLEAN TEXT FUNCTION
def clean_text(text, max_length=200):
    if not text:
        return "Not available"
    text = text.replace("\n", " ").strip()
    return text[:max_length] + "..." if len(text) > max_length else text


# 🔥 API FUNCTION (OpenFDA)
def fetch_medicine_from_api(name):
    if name in api_cache:
        return api_cache[name]

    url = "https://api.fda.gov/drug/label.json"

    try:
        params = {
            "search": f"openfda.generic_name:{name}",
            "limit": 1
        }

        res = requests.get(url, params=params, timeout=3)

        if res.status_code == 200:
            data = res.json()

            if "results" in data:
                r = data["results"][0]

                result = {
                    "name": name,
                    "category": "General",
                    "usage": clean_text(r.get("purpose", ["Not available"])[0], 100),
                    "dosage": clean_text(r.get("dosage_and_administration", ["Not available"])[0], 150),
                    "side_effects": clean_text(r.get("warnings", ["Not available"])[0], 200),
                    "source": "API"
                }

                api_cache[name] = result
                return result

    except Exception as e:
        print("API Error:", e)

    return None


# 🧠 EXTRACT MEDICINES
def extract_medicines(text):
    lines = text.split("\n")
    medicines = []

    for line in lines:
        clean_line = re.sub(r'[^a-zA-Z0-9 ./]', '', line).strip()

        if len(clean_line) < 3:
            continue

        words = clean_line.split()

        if words and words[0].replace(".", "").isdigit():
            words = words[1:]

        if len(words) < 1:
            continue

        med = " ".join(words[:3])
        medicines.append(med)

    return list(dict.fromkeys(medicines))


# 🧠 CORRECT NAMES
def correct_medicine_names(extracted):
    corrected = []
    suggestions = []

    for med in extracted:
        med_upper = med.upper()

        if med_upper in medicine_list:
            corrected.append(med_upper)
        else:
            matches = get_close_matches(med_upper, medicine_list, n=1, cutoff=0.7)

            if matches:
                corrected.append(matches[0])
                suggestions.append(f"{med} → {matches[0]}")
            else:
                corrected.append(med_upper)
                suggestions.append(f"{med} → No close match (API search)")

    return list(dict.fromkeys(corrected)), suggestions


# 🧠 GET DETAILS (LOCAL + API)
def get_medicine_details(names):
    results = []

    for name in names:
        found = False

        # LOCAL SEARCH
        for med in medicine_data:
            if med["name"] == name:
                med_copy = med.copy()
                med_copy["source"] = "Local"
                results.append(med_copy)
                found = True
                break

        # API FALLBACK
        if not found:
            api_data = fetch_medicine_from_api(name)

            if api_data:
                results.append(api_data)

    return results


# 🚨 NEW: DRUG INTERACTION CHECKER
def check_interactions(medicine_names):
    interactions_found = []

    for i in range(len(medicine_names)):
        for j in range(i + 1, len(medicine_names)):

            pair = (medicine_names[i], medicine_names[j])
            reverse_pair = (medicine_names[j], medicine_names[i])

            if pair in drug_interactions:
                interactions_found.append(
                    f"{pair[0]} + {pair[1]} → {drug_interactions[pair]}"
                )
            elif reverse_pair in drug_interactions:
                interactions_found.append(
                    f"{reverse_pair[0]} + {reverse_pair[1]} → {drug_interactions[reverse_pair]}"
                )

    return interactions_found


# 🌐 HOME PAGE
@app.route("/")
def home():
    return render_template("index.html", result=[], suggestions=[], interactions=[])


# 🔥 PROCESS ROUTE
@app.route("/process", methods=["POST"])
def process():
    result = []
    suggestions = []
    interactions = []   # ✅ NEW

    text_input = request.form.get("text_input")
    file = request.files.get("image")

    # IMAGE INPUT
    if file and file.filename != "":
        image = Image.open(file)

        try:
            text = pytesseract.image_to_string(image)
        except:
            text = ""

        raw_meds = extract_medicines(text)

    # TEXT INPUT
    elif text_input:
        raw_meds = [
            line.strip().upper()
            for line in text_input.split("\n")
            if line.strip()
        ]

    else:
        raw_meds = []

    # CORRECTION
    corrected, suggestions = correct_medicine_names(raw_meds)

    # GET DETAILS
    result = get_medicine_details(corrected)

    # 🚨 CHECK INTERACTIONS
    interactions = check_interactions(corrected)

    return render_template(
        "index.html",
        result=result,
        suggestions=suggestions,
        interactions=interactions
    )


if __name__ == "__main__":
    app.run(debug=True)