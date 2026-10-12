import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
from app.services.entity_classifier import EntityClassifier

client = httpx.Client(timeout=25.0, headers={'User-Agent': 'OpportunityPlatformResearch/1.0'})
q = """
[out:json][timeout:25];
(
  nwr(14.89,73.68,15.80,74.34)['sport'='futsal'];
  nwr(14.89,73.68,15.80,74.34)['leisure'='pitch']['sport'='soccer'];
  nwr(14.89,73.68,15.80,74.34)['leisure'='sports_centre'];
);
out center tags;
"""
r = client.post('https://overpass-api.de/api/interpreter', data={'data': q})
if r.is_success:
    elems = r.json().get('elements', [])
    print(f"Total elements returned: {len(elems)}")
    classified = []
    for e in elems:
        tags = e.get('tags', {})
        name = tags.get('name')
        if not name or name.startswith("Maruti"):
            continue
        c = EntityClassifier.classify_entity(
            name=name,
            category=tags.get('leisure', tags.get('sport', 'sports_venue')),
            raw_metadata=tags,
            source_provider="OPENSTREETMAP"
        )
        classified.append((name, c.entity_type, c.sales_eligibility_state, c.confidence, tags.get('website') or tags.get('contact:website'), tags.get('phone') or tags.get('contact:phone')))

    print(f"\nTotal named facilities: {len(classified)}")
    print("\n--- COMMERCIAL / SALES ELIGIBLE ---")
    comm = [x for x in classified if x[2] == "SALES_ELIGIBLE"]
    for x in comm:
        print(f"  {x[0]} -> {x[1]} ({x[2]}, conf={x[3]}) | Web: {x[4]} | Phone: {x[5]}")

    print("\n--- INSTITUTIONAL / RESEARCH ONLY ---")
    inst = [x for x in classified if x[2] in ["INSTITUTIONAL", "RESEARCH_ONLY"]]
    for x in inst:
        print(f"  {x[0]} -> {x[1]} ({x[2]}, conf={x[3]}) | Web: {x[4]}")

    print("\n--- UNKNOWN ---")
    unk = [x for x in classified if x[2] == "UNKNOWN_ENTITY_TYPE"]
    for x in unk:
        print(f"  {x[0]} -> {x[1]} ({x[2]}, conf={x[3]}) | Web: {x[4]}")
