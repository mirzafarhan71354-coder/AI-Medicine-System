from flask import Flask, render_template, request
import pytesseract
from PIL import Image
import re

# 🧠 NEW IMPORTS (AI correction)
from difflib import get_close_matches
from medicine_dataset import medicine_list

app = Flask(__name__)

# 🔴 Tesseract Path
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


# 🧠 STEP 1: EXTRACT MEDICINES
def extract_medicines(text):
    import re

    lines = text.split("\n")
    medicines = []

    for line in lines:
        clean_line = re.sub(r'[^a-zA-Z0-9 ./]', '', line).strip()

        if len(clean_line) < 5:
            continue

        lower = clean_line.lower()
        words = clean_line.split()

        # Remove numbering like 1, 78., etc.
        if words[0].replace(".", "").isdigit():
            words = words[1:]

        if len(words) < 2:
            continue

        # 🚫 Ignore obvious non-medicine lines
        ignore = [
            "fever", "headache", "patient", "date", "reg", "timing",
            "follow", "outside", "business", "road", "pune"
        ]
        if any(w in lower for w in ignore):
            continue

        # 🎯 MEDICINE DETECTION LOGIC
        is_medicine = False

        # Case 1: TAB / CAP / SYR present (even if OCR slightly wrong)
        if any(k in lower for k in ["tab", "cap", "syr", "co."]):
            is_medicine = True

        # Case 2: Contains dosage numbers (500, 10/58 etc.)
        if any(char.isdigit() for char in clean_line):
            is_medicine = True

        if not is_medicine:
            continue

        # 🧹 Remove unwanted words
        noise_words = ["morning", "night", "days", "food", "after", "pm", "am"]
        filtered = [w for w in words if w.lower() not in noise_words]

        if len(filtered) < 2:
            continue

        # Keep only first 2–3 words (medicine name)
        med = " ".join(filtered[:3])

        medicines.append(med)

    # ✅ Remove duplicates
    medicines = list(dict.fromkeys(medicines))

    return medicines


# 🧠 STEP 2: CORRECT MEDICINE NAMES (AI)
def correct_medicine_names(extracted):
    corrected = []

    for med in extracted:
        med_upper = med.upper()

        # 🔹 Step 1: strict match
        match = get_close_matches(med_upper, medicine_list, n=1, cutoff=0.6)

        # 🔹 Step 2: fallback match (ONLY if looks like medicine)
        if not match:
            if any(char.isdigit() for char in med_upper) or len(med_upper) > 6:
                match = get_close_matches(med_upper, medicine_list, n=1, cutoff=0.45)

        if match:
            corrected.append(match[0])

    # ✅ Remove duplicates
    final = []
    for m in corrected:
        if m not in final:
            final.append(m)

    return final


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
            result = correct_medicine_names(raw_meds)

        # ✍️ TEXT INPUT
        elif text_input:
            raw_meds = extract_medicines(text_input)
            result = correct_medicine_names(raw_meds)

    return render_template("index.html", result=result)


if __name__ == "__main__":
    app.run(debug=True)