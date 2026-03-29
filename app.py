from flask import Flask, render_template, request
import pytesseract
from PIL import Image
import re

from difflib import get_close_matches
from medicine_dataset import medicine_list, medicine_data

app = Flask(__name__)

# 🧠 STEP 1: EXTRACT MEDICINES (for OCR)
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


# 🧠 STEP 2: CORRECT NAMES + SUGGESTIONS
def correct_medicine_names(extracted):
    corrected = []
    suggestions = []

    for med in extracted:
        med_upper = med.upper()

        # ✅ Exact match
        if med_upper in medicine_list:
            corrected.append(med_upper)
        else:
            # 🔥 Fuzzy match (handles typos)
            matches = get_close_matches(med_upper, medicine_list, n=3, cutoff=0.4)

            if matches:
                corrected.append(matches[0])
                suggestions.append(f"{med} → {matches[0]}")
            else:
                suggestions.append(f"{med} → Not Found")

    return list(dict.fromkeys(corrected)), suggestions


# 🧠 STEP 3: GET FULL DETAILS
def get_medicine_details(names):
    results = []

    for name in names:
        for med in medicine_data:
            if med["name"] == name:
                results.append(med)

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

        # ✍️ TEXT INPUT (IMPORTANT FIX)
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

        # 🔥 FULL DETAILS
        result = get_medicine_details(corrected)

    return render_template("index.html", result=result, suggestions=suggestions)


if __name__ == "__main__":
    app.run(debug=True)
