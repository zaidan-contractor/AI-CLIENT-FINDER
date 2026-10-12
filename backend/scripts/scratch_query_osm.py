import httpx

client = httpx.Client(timeout=15.0, headers={"User-Agent": "OpportunityPlatformResearch/1.0"})
q = """
[out:json][timeout:15];
(
  nwr(14.89,73.68,15.80,74.34)["leisure"="sports_centre"];
  nwr(14.89,73.68,15.80,74.34)["sport"="futsal"];
  nwr(14.89,73.68,15.80,74.34)["sport"="soccer"];
);
out center tags;
"""
r = client.post("https://overpass-api.de/api/interpreter", data={"data": q})
print("Status:", r.status_code)
if r.is_success:
    elems = r.json().get("elements", [])
    print("Found total:", len(elems))
    named = [e for e in elems if e.get("tags", {}).get("name") and not e.get("tags", {}).get("name", "").startswith("Maruti")]
    print("Named non-car:", len(named))
    for e in named[:30]:
        t = e.get("tags", {})
        print(f"ID: {e.get('id')} | Name: {t.get('name')} | Sport: {t.get('sport')} | Leisure: {t.get('leisure')} | City: {t.get('addr:city', t.get('addr:suburb', 'Goa'))} | Phone: {t.get('phone', t.get('contact:phone'))} | Web: {t.get('website', t.get('contact:website'))}")
