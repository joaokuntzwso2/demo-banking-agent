"""Ingest policy corpus into durable SQLite; optional semantic vectors.

Usage:
  python scripts/ingest-policies.py
  OPENAI_API_KEY=... OPENAI_EMBEDDING_MODEL=text-embedding-3-small \
    python scripts/ingest-policies.py --embeddings

For hosted agents, mount/point POLICY_INDEX_PATH at a persistent location;
otherwise the container rebuilds a smaller offline index on first request.
"""
import os,sys,sqlite3,json,asyncio
from agents.retrieval import build_index, INDEX, docs
build_index()
async def embed():
    from openai import AsyncOpenAI
    key=os.getenv('OPENAI_API_KEY');model=os.getenv('OPENAI_EMBEDDING_MODEL')
    if not key or not model:raise SystemExit('Both OPENAI_API_KEY and OPENAI_EMBEDDING_MODEL are required for --embeddings')
    client=AsyncOpenAI(api_key=key,**({'base_url':os.getenv('OPENAI_URL')} if os.getenv('OPENAI_URL') else {}))
    with sqlite3.connect(INDEX) as db:
        entries=db.execute('SELECT rowid,text FROM policies').fetchall()
    reply=await client.embeddings.create(model=model,input=[row[1] for row in entries])
    if len(reply.data)!=len(entries):raise RuntimeError('Embedding count mismatch')
    with sqlite3.connect(INDEX) as db:
        for row,vector in zip(entries,reply.data):
            db.execute('UPDATE policies SET embedding=? WHERE rowid=?',(json.dumps(vector.embedding),row[0]))
        db.execute('INSERT OR REPLACE INTO metadata(name,value) VALUES("embedding_model",?)',(model,))
    print(f'Persisted {len(entries)} embeddings with {model}.')
if '--embeddings' in sys.argv:asyncio.run(embed())
print(f'{len(docs())} documents indexed at {INDEX}')
