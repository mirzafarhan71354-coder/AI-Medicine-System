from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_from_directory,
)
import pytesseract
from PIL import Image
import re
import requests
import threading
import webbrowser
import os
import secrets
from datetime import timedelta
from difflib import get_close_matches

# ✅ Tesseract path
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# 🔥 IMPORT FILES
from medicine_dataset import medicine_list, search_medicines
from drug_interactions import drug_interactions
from model import data as symptom_data, medicine_uses, predict_disease

app = Flask(__name__)
configured_secret = os.environ.get("CAREPOINT_SECRET_KEY")
if not configured_secret and os.environ.get("APP_ENV", "").lower() == "production":
    raise RuntimeError("Set CAREPOINT_SECRET_KEY to a long random secret in production.")
app.config.update(
    SECRET_KEY=configured_secret or secrets.token_hex(32),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("CAREPOINT_HTTPS", "").lower()
    in {"1", "true", "yes"},
    VAPID_PUBLIC_KEY=os.environ.get("VAPID_PUBLIC_KEY", ""),
    VAPID_PRIVATE_KEY=os.environ.get("VAPID_PRIVATE_KEY", ""),
    VAPID_SUBJECT=os.environ.get("VAPID_SUBJECT", ""),
    TWILIO_ACCOUNT_SID=os.environ.get("TWILIO_ACCOUNT_SID", ""),
    TWILIO_AUTH_TOKEN=os.environ.get("TWILIO_AUTH_TOKEN", ""),
    TWILIO_FROM_NUMBER=os.environ.get("TWILIO_FROM_NUMBER", ""),
    CAREPOINT_AI_API_KEY=os.environ.get("CAREPOINT_AI_API_KEY", ""),
    CAREPOINT_AI_BASE_URL=os.environ.get(
        "CAREPOINT_AI_BASE_URL", "https://api.openai.com/v1"
    ),
    CAREPOINT_AI_MODEL=os.environ.get("CAREPOINT_AI_MODEL", "gpt-4o-mini"),
)
if not configured_secret:
    app.logger.warning(
        "CAREPOINT_SECRET_KEY is unset; local sessions will be invalidated on restart."
    )

from account_routes import bp as accounts_blueprint, login_required
from doctor_assistance import (
    DoctorAssistanceConfigurationError,
    DoctorAssistanceProviderError,
    EMERGENCY_REPLY,
    SPECIALTY_DOCTORS,
    ask_medical_assistant,
)
from reminder_db import init_database

init_database(app)
app.register_blueprint(accounts_blueprint)


@app.route("/service-worker.js")
def service_worker():
    response = send_from_directory(app.static_folder, "service-worker.js")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.path.startswith("/doctor-assistance"):
        response.headers["Cache-Control"] = "private, no-store"
    if app.config["SESSION_COOKIE_SECURE"]:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

# 🔥 API CACHE
api_cache = {}
label_cache = {}

# 🧠 SYMPTOMS FROM THE TRAINING DATASET
symptom_examples = sorted(set(symptom_data["symptoms"].str.lower().str.strip()))

# 🔄 SYNONYMS
synonyms = {
    "high temperature": "high fever",
    "high temp": "high fever",
    "shortness of breath": "breathlessness",
    "difficulty breathing": "breathlessness",
    "trouble breathing": "breathlessness",
    "throwing up": "vomiting",
    "loose motion": "diarrhea",
    "loose stools": "diarrhea",
    "tired": "fatigue",
    "stomach ache": "stomach pain",
    "tummy ache": "stomach pain",
    "joints pain": "joint pain",
    "joint pains": "joint pain",
    "sneezes": "sneezing",
    "stuffy nose": "runny nose",
    "nasal congestion": "runny nose",
    "period cramps": "menstrual cramps",
    "sore muscles": "muscle soreness",
    "heartburn": "acid reflux",
    "cold": "cough"
}

# 🔥 CLEAN TEXT
def clean_text(text, max_length=200):
    if not text:
        return "Not available"
    text = text.replace("\n", " ").strip()
    return text[:max_length] + "..." if len(text) > max_length else text


# 🧠 NORMALIZE SYMPTOMS
def normalize_symptoms(text):
    text = text.lower()

    for key in synonyms:
        text = re.sub(rf"\b{re.escape(key)}\b", synonyms[key], text)

    return text


# 🔍 MATCH COMPLETE DATASET SYMPTOM PHRASES
def extract_supported_symptoms(text):
    normalized = normalize_symptoms(text)
    normalized = re.sub(r"\b(?:and|with|plus|a|an|the)\b", " ", normalized)
    remaining = f" {re.sub(r'\s+', ' ', normalized).strip()} "
    matches = []

    supported_phrases = set(symptom_examples) | {"joint pain"}
    for phrase in sorted(supported_phrases, key=lambda item: len(item.split()), reverse=True):
        pattern = rf"(?<!\w){re.escape(phrase)}(?!\w)"
        if re.search(pattern, remaining):
            matches.append(phrase)
            remaining = re.sub(pattern, " ", remaining)

    return matches


def is_valid_input(text):
    return bool(extract_supported_symptoms(text))


# 🔥 OPENFDA API
def fetch_medicine_from_api(name):
    if name in api_cache:
        return api_cache[name]

    url = "https://api.fda.gov/drug/label.json"
    search_term = re.sub(r"[^a-zA-Z0-9 -]", " ", name).strip()
    if not search_term:
        return None

    try:
        params = {
            "search": (
                f'(openfda.brand_name:"{search_term}" OR '
                f'openfda.generic_name:"{search_term}")'
            ),
            "limit": 1
        }

        res = requests.get(url, params=params, timeout=3)

        if res.status_code == 404:
            return None
        res.raise_for_status()
        data = res.json()

        if "results" in data:
            label = data["results"][0]
            openfda = label.get("openfda", {})
            result = {
                "name": (openfda.get("brand_name") or [name])[0],
                "brand_name": (openfda.get("brand_name") or [""])[0],
                "generic_name": (openfda.get("generic_name") or [""])[0],
                "active_ingredients": ", ".join(openfda.get("substance_name", [])),
                "manufacturer": ", ".join(openfda.get("manufacturer_name", [])),
                "dosage_form": ", ".join(openfda.get("dosage_form", [])),
                "category": ", ".join(openfda.get("pharm_class_epc", [])) or "Not available",
                "usage": clean_text(
                    (label.get("indications_and_usage") or ["Not available"])[0],
                    200
                ),
                "dosage": clean_text(
                    (label.get("dosage_and_administration") or ["Not available"])[0],
                    200
                ),
                "side_effects": clean_text(
                    (label.get("warnings") or ["Not available"])[0],
                    200
                ),
                "source": "FDA drug label",
                "verification_status": (
                    "Information is transcribed from an FDA drug label; "
                    "it does not establish availability in India."
                )
            }

            api_cache[name] = result
            return result

    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        print("API Error:", e)

    return None


def fetch_label_for_product(product_ndc):
    if product_ndc in label_cache:
        return label_cache[product_ndc]

    try:
        response = requests.get(
            "https://api.fda.gov/drug/label.json",
            params={
                "search": f'openfda.product_ndc:"{product_ndc}"',
                "limit": 1
            },
            timeout=3
        )
        if response.status_code == 404:
            label_cache[product_ndc] = None
            return None
        response.raise_for_status()
        results = response.json().get("results", [])
        label_cache[product_ndc] = results[0] if results else None
        return label_cache[product_ndc]
    except (requests.RequestException, ValueError, KeyError, IndexError) as error:
        print("FDA label lookup error:", error)
        return None


# 🧠 OCR MEDICINE EXTRACTION
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


# 🧠 NAME CORRECTION
def correct_medicine_names(extracted):
    corrected = []
    suggestions = []

    for med in extracted:
        med_upper = med.upper()

        if search_medicines(med, limit=1):
            corrected.append(med)
        else:
            matches = get_close_matches(med_upper, medicine_list, n=1, cutoff=0.7)

            if matches:
                corrected.append(matches[0])
                suggestions.append(f"{med} → {matches[0]}")
            else:
                corrected.append(med_upper)
                suggestions.append(f"{med} → No close match (API search)")

    return list(dict.fromkeys(corrected)), suggestions


# 🧠 GET DETAILS
def get_medicine_details(names):
    results = []
    seen = set()
    label_lookups = 0

    for name in names:
        matches = search_medicines(name, limit=5)
        if not matches:
            api_data = fetch_medicine_from_api(name)
            if api_data:
                matches = [api_data]

        for medicine in matches:
            key = medicine.get("product_id") or (
                f"{medicine.get('source')}:{medicine.get('name')}:"
                f"{medicine.get('strength')}:{medicine.get('dosage_form')}"
            )
            if key not in seen:
                seen.add(key)
                if medicine.get("product_ndc") and label_lookups < 3:
                    label_lookups += 1
                    label = fetch_label_for_product(medicine["product_ndc"])
                    if label:
                        medicine["usage"] = clean_text(
                            (label.get("indications_and_usage") or
                             label.get("purpose") or ["Not available in FDA label"])[0],
                            200
                        )
                        medicine["dosage"] = clean_text(
                            (label.get("dosage_and_administration") or
                             ["Not available in FDA label"])[0],
                            200
                        )
                        medicine["side_effects"] = clean_text(
                            (label.get("warnings") or
                             ["Not available in FDA label"])[0],
                            200
                        )
                        medicine["label_source"] = "FDA drug label"
                        medicine["verification_status"] += (
                            " Indications and warning excerpts, when available, "
                            "are retrieved from an FDA label."
                        )
                results.append(medicine)

    return results


# 🚨 INTERACTIONS
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


# 🌐 HOME
@app.route("/")
def home():
    return render_template("landing.html")


@app.route("/tools")
@login_required
def tools():
    return render_template(
        "index.html",
        result=[],
        suggestions=[],
        interactions=[],
        predictions=[],
        symptom_examples=symptom_examples,
        hospital_results=[],
        hospital_error=None,
        active_tool=None
    )


@app.route("/doctor-assistance", methods=["GET"])
@login_required
def doctor_assistance():
    response = app.make_response(
        render_template(
            "doctor_assistance.html",
            specialties=[
                {"name": specialty, "doctors": doctors}
                for specialty, doctors in SPECIALTY_DOCTORS.items()
            ],
            ai_configured=bool(app.config["CAREPOINT_AI_API_KEY"]),
        )
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/doctor-assistance/chat", methods=["POST"])
@login_required
def doctor_assistance_chat():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Send your question as a JSON request."}), 400
    try:
        answer = ask_medical_assistant(
            api_key=app.config["CAREPOINT_AI_API_KEY"],
            base_url=app.config["CAREPOINT_AI_BASE_URL"],
            model=app.config["CAREPOINT_AI_MODEL"],
            specialty=payload.get("specialty"),
            doctor=payload.get("doctor"),
            message=payload.get("message"),
            history=payload.get("history", []),
        )
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except DoctorAssistanceConfigurationError as error:
        return jsonify({"error": str(error)}), 503
    except DoctorAssistanceProviderError as error:
        app.logger.warning("Doctor Assistance provider error: %s", error)
        return jsonify({"error": str(error)}), error.http_status
    except requests.RequestException:
        app.logger.exception("Doctor Assistance AI transport request failed")
        return jsonify({
            "error": (
                "The AI assistant could not complete that reply. Please try again shortly."
            )
        }), 502

    response = jsonify({"answer": answer, "emergency": answer == EMERGENCY_REPLY})
    response.headers["Cache-Control"] = "private, no-store"
    return response


# 💊 MEDICINE ANALYZER
@app.route("/process", methods=["POST"])
@login_required
def process():
    result = []
    suggestions = []
    interactions = []

    text_input = request.form.get("text_input", "")
    file = request.files.get("image")

    if file and file.filename != "":
        image = Image.open(file)

        try:
            text = pytesseract.image_to_string(image)
        except:
            text = ""

        raw_meds = extract_medicines(text)

    elif text_input:
        raw_meds = [
            line.strip().upper()
            for line in text_input.split("\n")
            if line.strip()
        ]
    else:
        raw_meds = []

    corrected, suggestions = correct_medicine_names(raw_meds)
    result = get_medicine_details(corrected)
    interactions = check_interactions(corrected)

    return render_template(
        "index.html",
        result=result,
        suggestions=suggestions,
        interactions=interactions,
        predictions=[],
        hospital_results=[],
        hospital_error=None,
        active_tool="medicines",
        medicine_input=text_input
    )


# 🧠 SYMPTOM CHECKER (UPGRADED)
@app.route("/symptom-check", methods=["POST"])
@login_required
def symptom_check():
    symptom_input = request.form.get("symptoms", "")
    recognized_symptoms = extract_supported_symptoms(symptom_input)
    analysis_message = None

    if not symptom_input.strip():
        predictions = []
        analysis_message = "Describe what you are feeling, including where it hurts and any other symptoms."
    elif not recognized_symptoms:
        predictions = []
        analysis_message = (
            "I couldn't match that description to the symptoms in my dataset. "
            "For pain, please add where it is, when it started, and whether it followed an injury or activity."
        )
    else:
        predictions = predict_disease(" ".join(recognized_symptoms))

    return render_template(
        "index.html",
        result=[],
        suggestions=[],
        interactions=[],
        predictions=predictions,
        analysis_message=analysis_message,
        recognized_symptoms=recognized_symptoms,
        symptom_examples=symptom_examples,
        hospital_results=[],
        hospital_error=None,
        active_tool="symptoms",
        symptoms_input=symptom_input
    )


@app.route("/symptom-chat", methods=["POST"])
@login_required
def symptom_chat():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()

    if not message or len(message) > 2048:
        return jsonify({"error": "Enter a message under 2,048 characters."}), 400

    recognized_symptoms = extract_supported_symptoms(message)
    matching_rows = symptom_data[symptom_data["symptoms"].isin(recognized_symptoms)]
    matches_by_condition = {}
    matches = []
    urgent = bool(re.search(
        r"\b(?:chest pain|breathlessness|shortness of breath|difficulty breathing|trouble breathing)\b",
        message,
        re.IGNORECASE
    )) or bool(re.search(r"\bchest\b", message, re.IGNORECASE) and re.search(
        r"\b(?:pain|ache|aching|hurts?)\b", message, re.IGNORECASE
    ))

    if urgent:
        reply = (
            "Chest pain or difficulty breathing can be an emergency. Contact your local emergency "
            "services now, especially if symptoms are new, severe, worsening, or accompanied by "
            "faintness, sweating, nausea, or pain spreading to your arm, jaw, or back. "
            "Do not wait for a chatbot to assess this. I cannot recommend a medicine for these symptoms."
        )
    else:
        for _, row in matching_rows.iterrows():
            condition_name = str(row["disease"])
            match = matches_by_condition.setdefault(condition_name, {
                "condition": condition_name,
                "symptoms": [],
                "medicine": str(row["medicine"]),
                "medicine_use": medicine_uses.get(
                    str(row["medicine"]).lower(),
                    "Use only as directed by a healthcare professional."
                )
            })
            symptom = str(row["symptoms"])
            if symptom not in match["symptoms"]:
                match["symptoms"].append(symptom)

        matches = list(matches_by_condition.values())
        if matches:
            for match in matches:
                match["symptoms"] = ", ".join(match["symptoms"])
            reply = (
                f"I recognized: {', '.join(recognized_symptoms)}. "
                "The entries below come from the local symptoms.csv reference data; they are not "
                "diagnoses or treatment recommendations."
            )
        else:
            reply = (
                "I couldn't match that description to a symptom record in the local dataset, "
                "so I won't guess a condition or medicine. Try describing a specific symptom."
            )

    return jsonify({
        "reply": reply,
        "matches": [] if urgent else matches,
        "urgent": urgent
    })


@app.route("/hospital-search", methods=["POST"])
@login_required
def hospital_search():
    location_query = request.form.get("location", "").strip()
    hospital_results = []
    hospital_error = None

    if not location_query:
        hospital_error = "Enter a city, address, or postal code to search."
    else:
        try:
            geocode_response = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": location_query, "format": "jsonv2", "limit": 1},
                headers={"User-Agent": "AI-Medicine-System/1.0"},
                timeout=10
            )
            geocode_response.raise_for_status()
            locations = geocode_response.json()

            if not locations:
                hospital_error = "We couldn't find that location. Try a nearby city or address."
            else:
                latitude = float(locations[0]["lat"])
                longitude = float(locations[0]["lon"])
                query = (
                    f'[out:json][timeout:10];(node["amenity"="hospital"]'
                    f'(around:5000,{latitude},{longitude});way["amenity"="hospital"]'
                    f'(around:5000,{latitude},{longitude});relation["amenity"="hospital"]'
                    f'(around:5000,{latitude},{longitude}););out center tags;'
                )
                hospitals_response = requests.get(
                    "https://overpass-api.de/api/interpreter",
                    params={"data": query},
                    headers={"User-Agent": "AI-Medicine-System/1.0"},
                    timeout=20
                )
                hospitals_response.raise_for_status()
                elements = hospitals_response.json().get("elements", [])

                for hospital in elements:
                    tags = hospital.get("tags", {})
                    coordinates = hospital.get("center", hospital)
                    if "lat" not in coordinates or "lon" not in coordinates:
                        continue
                    hospital_results.append({
                        "name": tags.get("name", "Hospital"),
                        "address": ", ".join(filter(None, [
                            tags.get("addr:street"), tags.get("addr:city")
                        ])),
                        "lat": coordinates["lat"],
                        "lon": coordinates["lon"]
                    })
                    if len(hospital_results) == 10:
                        break

                if not hospital_results:
                    hospital_error = "No hospitals were listed near that location."
        except (requests.RequestException, ValueError, KeyError) as error:
            print("Hospital search error:", error)
            hospital_error = "Hospital search is temporarily unavailable. Please try again."

    return render_template(
        "index.html",
        result=[],
        suggestions=[],
        interactions=[],
        predictions=[],
        hospital_results=hospital_results,
        hospital_error=hospital_error,
        active_tool="hospitals",
        location_query=location_query
    )


# 🚀 RUN
if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    threading.Timer(1, lambda: webbrowser.open_new(url)).start()
    debug_enabled = os.environ.get("APP_DEBUG", "").lower() in {
        "1", "true", "yes",
    }
    if os.environ.get("APP_ENV", "").lower() == "production":
        debug_enabled = False
    app.run(debug=debug_enabled, use_reloader=False)