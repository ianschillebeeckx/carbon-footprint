"""Embed the NAICS index for the Worker's vector candidate tier.

Uses Cloudflare Workers AI (@cf/baai/bge-small-en-v1.5) over each entry's
enriched search_text — the SAME hosted model the Worker uses for query
embeddings at classify time, so the two spaces match exactly. Output:
web/data/naics_vectors.json {dim, codes, titles, vec_b64} with L2-normalized
float32 rows, base64-encoded.

Run:  CF_TOKEN=... CF_ACCOUNT=... .venv/bin/python scripts/build_worker_vectors.py
(The token needs the Workers AI Read permission.)
"""

import base64
import json
import os
import struct
import urllib.request
from pathlib import Path

INDEX = Path("data/naics_index.json")
OUT = Path("web/data/naics_vectors.json")
MODEL = "@cf/baai/bge-small-en-v1.5"
BATCH = 100

token = os.environ["CF_TOKEN"]
account = os.environ.get("CF_ACCOUNT", "b5135b840411bc8283900d69a4638c5f")
url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODEL}"

entries = json.loads(INDEX.read_text())["entries"]
texts = [e["search_text"] for e in entries]
vectors: list[list[float]] = []
for i in range(0, len(texts), BATCH):
    req = urllib.request.Request(url, data=json.dumps({"text": texts[i:i + BATCH]}).encode(),
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    assert d.get("success"), d.get("errors")
    vectors.extend(d["result"]["data"])
    print(f"embedded {len(vectors)}/{len(texts)}")

dim = len(vectors[0])
buf = bytearray()
for v in vectors:
    norm = sum(x * x for x in v) ** 0.5 or 1.0
    buf += struct.pack(f"<{dim}f", *(x / norm for x in v))

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({
    "model": MODEL, "dim": dim,
    "codes": [e["code"] for e in entries],
    "titles": [e["title"] for e in entries],
    "vec_b64": base64.b64encode(bytes(buf)).decode(),
}))
print(f"Wrote {OUT}: {len(entries)} codes x {dim} dims, {OUT.stat().st_size:,} bytes")
