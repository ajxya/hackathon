"""EDFlow injury/tier generator — data for the patient generator only.

The full pool below is never displayed in the UI; only the single injury
assigned to an individual generated patient is shown for that patient.
Everything here is a made-up category label for synthetic demo patients,
not real medical data.
"""

import random

INJURIES_BY_TIER = {
    1: [
        "Cardiac arrest", "Massive hemorrhage", "Major trauma with shock",
        "STEMI / acute myocardial infarction", "Aortic dissection", "Massive pulmonary embolism",
        "Severe sepsis/septic shock", "Severe anaphylaxis", "Acute respiratory failure",
        "Tension pneumothorax", "Major intracranial hemorrhage", "Status epilepticus",
        "Severe traumatic brain injury", "Ruptured abdominal aortic aneurysm", "Severe airway obstruction",
        "Major burns", "Severe poisoning/overdose", "Severe hypoglycemia with altered consciousness",
        "Severe hyperkalemia with arrhythmia", "Near-drowning with respiratory compromise",
    ],
    2: [
        "Stroke / acute neurologic deficit", "Severe asthma exacerbation", "Severe COPD exacerbation",
        "Acute heart failure / pulmonary edema", "Serious cardiac arrhythmia", "Unstable GI bleeding",
        "Severe sepsis without shock", "Ectopic pregnancy with suspected rupture", "Severe diabetic ketoacidosis",
        "Severe hyperosmolar hyperglycemic state", "Meningitis", "Encephalitis", "Significant head injury",
        "Spinal cord injury", "Pelvic fracture", "Open fracture", "Major femur fracture",
        "Compartment syndrome", "Severe allergic reaction", "Significant facial/neck trauma",
    ],
    3: [
        "Chest pain", "Shortness of breath", "Moderate abdominal pain", "Significant dehydration",
        "Moderate asthma exacerbation", "Pneumonia", "Kidney stone", "Appendicitis", "Cholecystitis",
        "Pancreatitis", "GI bleeding without instability", "Moderate infection",
        "High fever with concerning symptoms", "Moderate allergic reaction", "Concussion",
        "Closed fracture", "Dislocation", "Deep laceration", "Significant burns", "Moderate back injury",
    ],
    4: [
        "Sprain", "Strain", "Contusion / bruise", "Minor fracture", "Superficial laceration", "Minor burn",
        "Minor head injury", "Minor extremity injury", "Back pain", "Migraine", "Headache", "Ear infection",
        "Sinus infection", "Sore throat", "Viral respiratory infection", "Cough", "Nausea/vomiting",
        "Diarrhea", "Mild abdominal pain", "Urinary tract infection",
    ],
    5: [
        "Mild dehydration", "Mild fever", "Rash", "Minor allergic reaction", "Minor wound infection",
        "Pink eye / conjunctivitis", "Mild ear pain", "Minor dental pain", "Minor back pain",
        "Minor joint pain", "Mild muscle pain", "Medication refill/problem", "Anxiety/panic symptoms",
        "Mild dizziness", "Mild fatigue/weakness", "Minor nosebleed", "Minor insect bite",
        "Minor foreign body injury", "Mild nausea", "Mild viral symptoms",
    ],
}

# Tier-selection weights by arrival source. Ambulance arrivals skew more
# severe (tiers 1-3); walk-ins skew less severe (tiers 3-5). Combined, tiers
# 4-5 end up far more common than tier 1, matching a realistic ED mix.
AMBULANCE_TIER_WEIGHTS = {1: 15, 2: 30, 3: 35, 4: 15, 5: 5}
WALKIN_TIER_WEIGHTS = {1: 1, 2: 4, 3: 20, 4: 45, 5: 30}


def random_tier(source):
    weights = AMBULANCE_TIER_WEIGHTS if source == "ambulance" else WALKIN_TIER_WEIGHTS
    tiers = list(weights.keys())
    return random.choices(tiers, weights=list(weights.values()), k=1)[0]


def random_injury(tier):
    return random.choice(INJURIES_BY_TIER[tier])
