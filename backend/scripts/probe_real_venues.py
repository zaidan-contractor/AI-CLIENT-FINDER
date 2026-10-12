import httpx

venues = [
    {"name": "Nagoa Futsal Arena", "location": "Nagoa, Goa", "query": "Nagoa Futsal Arena Goa"},
    {"name": "Agnel Futsal Arena", "location": "Assagao, Goa", "query": "Agnel Futsal Arena Goa"},
    {"name": "Penha De Franca Futsal Arena", "location": "Porvorim, Goa", "query": "Penha De Franca Futsal Arena Goa"},
    {"name": "DENGOA Multisports Arena", "location": "Ribandar, Goa", "query": "DENGOA Multisports Arena Goa"},
    {"name": "Galacticos Sports Club", "location": "Porvorim, Goa", "query": "Galacticos Sports Club Goa"},
    {"name": "Rosa Arena", "location": "Dabolim, Goa", "query": "Rosa Arena Goa"},
    {"name": "Camp Souza Futsal Turf", "location": "Camorlim, Goa", "query": "Camp Souza Futsal Turf Goa"},
    {"name": "Don Bosco Oratory Futsal Turf", "location": "Panaji, Goa", "query": "Don Bosco Oratory Futsal Panaji Goa"},
    {"name": "Kitex Kick-off Arena", "location": "Talaulim, Goa", "query": "Kitex Kick-off Arena Goa"},
    {"name": "L D Silva Arena", "location": "Benaulim, Goa", "query": "L D Silva Arena Goa"}
]

print("Probing venues...")
