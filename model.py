import pandas as pd
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.naive_bayes import MultinomialNB

# Load dataset
data = pd.read_csv('symptoms.csv')

# Text cleaning
def clean_text(text):
    return text.lower().strip()

# Synonym handling
synonyms = {
    "throwing up": "vomiting",
    "loose motion": "diarrhea",
    "loose stools": "diarrhea",
    "tired": "fatigue",
    "stomach ache": "stomach pain",
    "tummy ache": "stomach pain",
    "high temperature": "high fever",
    "high temp": "high fever",
    "shortness of breath": "breathlessness",
    "difficulty breathing": "breathlessness",
    "trouble breathing": "breathlessness",
    "sneezes": "sneezing",
    "cold": "cough"
}

def replace_synonyms(text):
    text = text.lower()
    for key in synonyms:
        if key in text:
            text = text.replace(key, synonyms[key])
    return text

# Prepare data
data['symptoms'] = data['symptoms'].apply(clean_text)

medicine_uses = {
    "paracetamol": "May reduce fever and relieve mild pain.",
    "ibuprofen": "May relieve pain, fever, and inflammation.",
    "chloroquine": "Used for certain malaria infections; take only when prescribed after medical evaluation.",
    "cetirizine": "May relieve allergy symptoms such as sneezing and a runny nose.",
    "loratadine": "May relieve allergy symptoms such as sneezing and a runny nose.",
    "inhaler": "May help open the airways in asthma; use the inhaler prescribed to the patient.",
    "antibiotics": "Treat some bacterial infections, not viral illnesses, and require clinical direction.",
    "ors": "Helps replace fluids and salts lost through diarrhea or vomiting.",
    "antacid": "May relieve symptoms of stomach acid or indigestion.",
    "iron supplements": "Replace iron for iron deficiency when advised by a healthcare professional.",
    "aspirin": "Used in certain heart-related situations under urgent medical guidance; it is not suitable for everyone.",
    "metformin": "Helps manage blood glucose in type 2 diabetes when prescribed.",
    "amoxicillin": "Treats some bacterial infections, not viral illnesses, and must be prescribed.",
    "eye drops": "May relieve certain eye symptoms depending on the product; use as advised by a healthcare professional.",
    "fiber supplement": "May support regular bowel movements; follow the product label and ask a healthcare professional if symptoms persist.",
    "antifungal cream": "Treats some fungal skin infections; use only as directed on the product label or by a healthcare professional.",
    "saline nasal spray": "May moisturize nasal passages and loosen mucus; follow the product label."
}

X = data['symptoms']
y = data['disease']

# 🔥 IMPROVED VECTORIZER
vectorizer = CountVectorizer(ngram_range=(1,2))
X_vectorized = vectorizer.fit_transform(X)

# Train model
model = MultinomialNB()
model.fit(X_vectorized, y)

# 🔥 IMPROVED PREDICTION FUNCTION
def predict_disease(user_input):
    user_input = clean_text(user_input)
    user_input = replace_synonyms(user_input)

    input_vector = vectorizer.transform([user_input])

    probabilities = model.predict_proba(input_vector)[0]
    classes = model.classes_

    top_indices = probabilities.argsort()[-3:][::-1]

    results = []

    for i in top_indices:
        disease = classes[i]
        disease_rows = data[data['disease'] == disease]
        row_vectors = X_vectorized[disease_rows.index.to_numpy()]
        overlap_scores = (row_vectors @ input_vector.T).toarray().ravel()
        row = disease_rows.iloc[int(overlap_scores.argmax())]
        medicine = row['medicine']

        results.append({
            "disease": disease,
            "matched_symptoms": row["symptoms"],
            "medicine": medicine,
            "medicine_use": medicine_uses.get(
                str(medicine).lower(),
                "Use only as directed by a healthcare professional."
            ),
            "severity": row['severity'],
        })

    return results