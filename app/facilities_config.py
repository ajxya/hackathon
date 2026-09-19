"""EDFlow facility directory — every nearby facility EDFlow knows about.

Everything here is fictional and simulated: no real hospitals, addresses,
or people. Adding a new facility later should only require adding an
entry to FACILITIES — nothing else in the app needs to change.
"""

HOME_ED = {
    "id": "home",
    "name": "EDFlow Emergency Department",
    "lat": 37.2296,
    "lng": -80.4139,
}

FACILITIES = [
    {
        "id": "riverside",
        "name": "Riverside Regional Medical Center",
        "type": "hospital",
        "lat": 37.2796,
        "lng": -80.3539,
        "travel_time_minutes": 12,
        "distance_miles": 4.8,
        "capabilities": ["trauma", "stroke"],
        "base_load": 0.55,
    },
    {
        "id": "northgate",
        "name": "Northgate Medical Center",
        "type": "hospital",
        "lat": 37.3296,
        "lng": -80.4339,
        "travel_time_minutes": 18,
        "distance_miles": 7.2,
        "capabilities": ["cardiac", "pediatric"],
        "base_load": 0.45,
    },
    {
        "id": "westside-urgent-care",
        "name": "Westside Urgent Care",
        "type": "urgent_care",
        "lat": 37.1996,
        "lng": -80.4639,
        "travel_time_minutes": 9,
        "distance_miles": 3.1,
        "capabilities": ["urgent_care"],
        "base_load": 0.4,
    },
    {
        "id": "eastview-urgent-care",
        "name": "Eastview Urgent Care",
        "type": "urgent_care",
        "lat": 37.2396,
        "lng": -80.3239,
        "travel_time_minutes": 14,
        "distance_miles": 5.5,
        "capabilities": ["urgent_care"],
        "base_load": 0.35,
    },
]
