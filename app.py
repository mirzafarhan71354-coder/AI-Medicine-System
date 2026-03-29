from flask import Flask, render_template, request
import pytesseract
from PIL import Image
import re

# 🧠 AI Matching
from difflib import get_close_matches
from medicine_dataset import medicine_list, medicine_data

app = Flask(__name__)

# 🔴 Tesseract Path (uncomment if needed)
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


# 🧠 STEP 1: EXTRACT MEDICINES
def extract_medicines(text):
    lines = text.split("\n")
    medicines = []

    for line in lines:
        clean_line = re.sub(r'[^a-zA-Z0-9 ./]', '', line).strip()

        if len(clean_line) < 5:
            continue

        lower = clean_line.lower()
        words = clean_line.split()

        # Remove numbering
        if words and words[0].replace(".", "").isdigit():
            words = words[1:]

        if len(words) < 2:
            continue

        # Ignore non-medical lines
        ignore = [
            "fever", "headache", "patient", "date", "reg", "timing",
            "follow", "outside", "business", "road", "pune"
        ]
        if any(w in lower for w in ignore):
            continue

        # Detect medicine
        is_medicine = False

        if any(k in lower for k in ["tab", "cap", "syr", "co."]):
            is_medicine = True

        if any(char.isdigit() for char in clean_line):
            is_medicine = True

        if not is_medicine:
            continue

        # Remove noise
        noise_words = ["morning", "night", "days", "food", "after", "pm", "am"]
        filtered = [w for w in words if w.lower() not in noise_words]

        if len(filtered) < 2:
            continue

        med = " ".join(filtered[:3])
        medicines.append(med)

    return list(dict.fromkeys(medicines))


# 🧠 STEP 2: CORRECT MEDICINE NAMES
def correct_medicine_names(extracted):
    corrected = []

    for med in extracted:
        med_upper = med.upper()

        match = get_close_matches(med_upper, medicine_list, n=1, cutoff=0.6)

        if not match:
            if any(char.isdigit() for char in med_upper) or len(med_upper) > 6:
                match = get_close_matches(med_upper, medicine_list, n=1, cutoff=0.45)

        if match:
            corrected.append(match[0])

    return list(dict.fromkeys(corrected))


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
            corrected = correct_medicine_names(raw_meds)
            result = get_medicine_details(corrected)

        # ✍️ TEXT INPUT
        elif text_input:
            raw_meds = extract_medicines(text_input)
            corrected = correct_medicine_names(raw_meds)
            result = get_medicine_details(corrected)

    return render_template("index.html", result=result)


if __name__ == "__main__":
    app.run(debug=True)