import httpx
import time

client = httpx.Client(timeout=10.0, headers={"User-Agent": "OpportunityPlatformResearch/1.0 (compliance@platform.test)"})
queries = [
    "futsal Goa",
    "football ground Goa",
    "sports complex Goa",
    "sports club Goa",
    "indoor stadium Goa",
    "stadium Goa",
    "turf Goa"
]

all_items = {}
for q in queries:
    try:
        r = client.get("https://nominatim.openstreetmap.org/search", params={"q": q, "format": "json", "addressdetails": 1, "extratags": 1, "limit": 10})
        if r.is_success:
            items = r.json()
            print(f"Query '{q}': {len(items)} results")
            for it in items:
                name = it.get("name") or it.get("display_name", "").split(",")[0]
                osm_id = it.get("osm_id") or it.get("place_id")
                if osm_id not in all_items:
                    all_items[osm_id] = {
                        "name": name,
                        "display_name": it.get("display_name"),
                        "extratags": it.get("extratags", {}),
                        "address": it.get("address", {}),
                        "query": q
                    }
        else:
            print(f"Query '{q}' failed: {r.status_code}")
    except Exception as e:
        print(f"Query '{q}' error: {e}")
    time.sleep(1.1)

print(f"\nTotal unique real facilities found: {len(all_items)}")
for osm_id, data in list(all_items.items())[:20]:
    addr = data["address"]
    city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county") or "Goa"
    print(f"- {data['name']} | City: {city} | Matched via: '{data['query']}' | Extratags: {data['extratags']}")
