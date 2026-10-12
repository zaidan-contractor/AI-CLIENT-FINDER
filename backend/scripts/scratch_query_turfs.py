import httpx
import time

client = httpx.Client(timeout=10.0, headers={'User-Agent': 'OpportunityPlatformResearch/1.0 (compliance@platform.test)'})
queries = ['turf Goa', 'football turf Goa', 'futsal Goa', 'sports arena Goa', 'sports club Goa']
for q in queries:
    r = client.get('https://nominatim.openstreetmap.org/search', params={'q': q, 'format': 'json', 'addressdetails': 1, 'extratags': 1, 'limit': 10})
    if r.is_success:
        items = r.json()
        print(f"=== Query: {q} ({len(items)} results) ===")
        for it in items:
            name = it.get('name') or it.get('display_name', '').split(',')[0]
            print(f"  - {name} | type: {it.get('type')} | osm_id: {it.get('osm_id')} | web: {it.get('extratags', {}).get('website')}")
    time.sleep(1.0)
