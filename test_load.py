import requests
import time
from concurrent.futures import ThreadPoolExecutor

# Test the main API endpoints for speed under load
BASE_URL = "http://127.0.0.1:5001"
ENDPOINTS = [
    "/api/sector-performance",
    "/api/r-factor",
    "/api/heatmap",
    "/api/institutional-zones"
]

CONCURRENT_USERS = 50 
TOTAL_REQUESTS = 200

def fetch(url):
    start = time.time()
    try:
        resp = requests.get(url, timeout=5)
        took = (time.time() - start) * 1000
        return resp.status_code, took
    except Exception as e:
        return 500, 0

print(f"Starting Load Test: {CONCURRENT_USERS} concurrent users, {TOTAL_REQUESTS} total requests...")

results = []
start_total = time.time()

with ThreadPoolExecutor(max_workers=CONCURRENT_USERS) as executor:
    futures = []
    for _ in range(TOTAL_REQUESTS):
        for ep in ENDPOINTS:
            futures.append(executor.submit(fetch, BASE_URL + ep))
            
    for f in futures:
        results.append(f.result())

end_total = time.time()
total_time = end_total - start_total
successful = [r for r in results if r[0] == 200]
avg_latency = sum(r[1] for r in successful) / len(successful) if successful else 0

print("\n" + "="*40)
print(f"LOAD TEST RESULTS")
print("="*40)
print(f"Total Requests:      {len(results)}")
print(f"Successful:          {len(successful)}")
print(f"Avg Latency:         {avg_latency:.2f} ms")
print(f"Total Execution:     {total_time:.2f} seconds")
print(f"Throughput:          {len(results)/total_time:.2f} req/s")
print("="*40)

if len(successful) == len(results) and avg_latency < 200:
    print("✅ SUCCESS: Server handled high concurrency with sub-200ms latency.")
else:
    print("⚠️ WARNING: High latency or errors detected under load.")
