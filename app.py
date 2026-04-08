from flask import Flask, render_template, request
import pytesseract
from PIL import Image
import re
import requests

from difflib import get_close_matches
from medicine_dataset import medicine_list, medicine_data

from api_service import fetch_medicine_from_api

app = Flask(__name__)

# 🔥 API CACHE
api_cache = {}

# 🔥 CLEAN TEXT FUNCTION (shortens long API responses)
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

        res = requests.get(url, params=params)

        if res.status_code == 200:
            data = res.json()

            if "results" in data:
                r = data["results"][0]

                result = {
                    "name": name,
                    "category": "General",
                    "uses": clean_text(r.get("purpose", ["Not available"])[0], 100),
                    "dosage": clean_text(r.get("dosage_and_administration", ["Not available"])[0], 150),
                    "side_effects": clean_text(r.get("warnings", ["Not available"])[0], 200),
                    "source": "API"
                }

                api_cache[name] = result
                return result

    except Exception as e:
        print("API Error:", e)

    return None


# 🧠 STEP 1: EXTRACT MEDICINES (OCR)
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


# 🧠 STEP 2: CORRECT NAMES (IMPROVED)
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


# 🧠 STEP 3: GET DETAILS (LOCAL + API)
def get_medicine_details(names):
    results = []

    for name in names:
        found = False

        # ✅ LOCAL SEARCH
        for med in medicine_data:
            if med["name"] == name:
                med_copy = med.copy()
                med_copy["source"] = "Local"
                results.append(med_copy)
                found = True
                break

        # 🔥 API FALLBACK
        if not found:
            api_data = fetch_medicine_from_api(name)

            if api_data:
                results.append(api_data)

    return results


# 🌐 MAIN ROUTE
@app.route("/", methods=["GET", "POST"])
def index():
    result = []
    suggestions = []

    if request.method == "POST":
        text_input = request.form.get("text_input")
        file = request.files.get("image")

        # 📷 IMAGE INPUT
        if file and file.filename != "":
            image = Image.open(file)

            try:
                text = pytesseract.image_to_string(image)
            except:
                text = ""

            raw_meds = extract_medicines(text)

        # ✍️ TEXT INPUT
        elif text_input:
            raw_meds = [
                line.strip().upper()
                for line in text_input.split("\n")
                if line.strip()
            ]

        else:
            raw_meds = []

        # 🔥 CORRECTION + SUGGESTIONS
        corrected, suggestions = correct_medicine_names(raw_meds)

        # 🔥 GET RESULTS
        result = get_medicine_details(corrected)

    return render_template("index.html", result=result, suggestions=suggestions)


if __name__ == "__main__":
    app.run(debug=True)