"""Persistent, versioned bank-policy retrieval.

Default: SQLite FTS5 full-text index with explicit citations, no model needed.
Optional: persistent OpenAI embedding vectors (run scripts/ingest-policies.py
with OPENAI_EMBEDDING_MODEL); falls back truthfully to lexical ranking.
"""
import json,os,sqlite3,hashlib,re,math
from pathlib import Path
from functools import lru_cache
from .confirmation import money
from decimal import Decimal

CORPUS=Path(__file__).resolve().parent.parent/'knowledge'/'policies.json'
INDEX=Path(os.getenv('POLICY_INDEX_PATH','/tmp/aurora-policy-index.sqlite'))

def docs():return json.loads(CORPUS.read_text())

def build_index():
    INDEX.parent.mkdir(parents=True,exist_ok=True)
    items=docs();digest=hashlib.sha256(CORPUS.read_bytes()).hexdigest()
    with sqlite3.connect(INDEX) as db:
        db.execute('CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS policies (rowid INTEGER PRIMARY KEY,id TEXT,version TEXT,topic TEXT,language TEXT,effective TEXT,text TEXT,embedding TEXT)')
        db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS policy_fts USING fts5(text, topic, content="policies", content_rowid="rowid", tokenize="unicode61")')
        previous=db.execute('SELECT value FROM metadata WHERE name="sha256"').fetchone()
        if previous and previous[0]==digest:return
        db.execute('DELETE FROM policy_fts');db.execute('DELETE FROM policies')
        for doc in items:
            cur=db.execute('INSERT INTO policies(id,version,topic,language,effective,text,embedding) VALUES(?,?,?,?,?,?,NULL)',
                           (doc['id'],doc['version'],doc['topic'],doc['language'],doc.get('effective','2026-01-01'),doc['text']))
            db.execute('INSERT INTO policy_fts(rowid,text,topic) VALUES(?,?,?)',(cur.lastrowid,doc['text'],doc['topic']))
        db.execute('INSERT OR REPLACE INTO metadata(name,value) VALUES("sha256",?)',(digest,))

def tokenize(text):return re.findall(r'\w+',text.lower(),flags=re.UNICODE)

def retrieve_policy(topic,lang,query,top_k=3):
    build_index()
    language='es' if lang=='es' else 'en'
    terms=tokenize(query)
    with sqlite3.connect(INDEX) as db:
        db.row_factory=sqlite3.Row
        indexed=[]
        if terms:
            # Safe OR expression built from regex-extracted word tokens only.
            expression=' OR '.join('"'+term+'"' for term in list(dict.fromkeys(terms))[:16])
            sql=("SELECT p.*, (-bm25(policy_fts)) AS relevance "
                 "FROM policy_fts JOIN policies p ON p.rowid=policy_fts.rowid "
                 "WHERE policy_fts MATCH ? AND p.topic=? AND p.language=? "
                 "ORDER BY bm25(policy_fts) LIMIT ?")
            try:
                indexed=[dict(row) for row in db.execute(sql,(expression,topic,language,top_k))]
            except sqlite3.OperationalError:
                indexed=[]
        all_docs=[dict(row) for row in db.execute(
            'SELECT * FROM policies WHERE topic=? AND language=? ORDER BY id',(topic,language))]
    if not all_docs:raise ValueError('No policy supports this topic/language')
    tokens=set(terms)
    scoring=[]
    for item in all_docs:
        overlap=len(tokens & set(tokenize(item['text'])))
        score=overlap / max(1,math.sqrt(len(tokens)))
        if item['id'] in ('POL-FIN-001','POL-DIS-001'):score+=.25
        scoring.append((score,item))
    # Add nonmatching relevant records only if FTS did not return enough.
    scored={d['id']:(float(d['relevance']),d) for d in indexed}
    for score,item in sorted(scoring,key=lambda r:r[0],reverse=True):
        if len(scored)>=top_k:break
        if item['id'] not in scored:scored[item['id']]=(score,item)
    chosen=[]
    # A base cashflow/dispute policy must be present in the retrieved evidence
    # for the stable grounded account/dispute explanation and its citation.
    primary='POL-FIN-001' if topic=='wellness' else 'POL-DIS-001'
    baseline=next((d for d in all_docs if d['id']==primary),None)
    if baseline:chosen.append((.01,baseline))
    for score,item in sorted(scored.values(),key=lambda pair:pair[0],reverse=True):
        if all(item['id']!=d['id'] for _,d in chosen):chosen.append((score,item))
        if len(chosen)==top_k:break
    return [{**item,'retrieval_method':'persistent_fts5_bm25','score':round(score,3)}
            for score,item in chosen[:top_k]]

async def semantic_retrieve(topic,lang,query):
    """Use persisted embeddings only when their model matches the configured query model."""
    model=os.getenv('OPENAI_EMBEDDING_MODEL','')
    if not model or not os.getenv('OPENAI_API_KEY'):return None
    build_index()
    with sqlite3.connect(INDEX) as db:
        db.row_factory=sqlite3.Row
        saved=db.execute('SELECT value FROM metadata WHERE name="embedding_model"').fetchone()
        if not saved or saved[0]!=model:return None
        items=[dict(r) for r in db.execute('SELECT * FROM policies WHERE topic=? AND language=?',
                                          (topic,'es' if lang=='es' else 'en'))]
    if not items or any(not d['embedding'] for d in items):return None
    from openai import AsyncOpenAI
    client=AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'),
        **({'base_url':os.getenv('OPENAI_URL')} if os.getenv('OPENAI_URL') else {}))
    result=await client.embeddings.create(model=model,input=query)
    vector=result.data[0].embedding
    def cosine(a,b):
        dot=sum(x*y for x,y in zip(a,b))
        norma=math.sqrt(sum(x*x for x in a));normb=math.sqrt(sum(x*x for x in b))
        return dot/(norma*normb) if norma and normb else 0
    found=sorted(((cosine(vector,json.loads(d['embedding'])),d) for d in items),reverse=True,key=lambda p:p[0])[:3]
    return [{**d,'retrieval_method':'cached_semantic_embeddings','score':round(score,3)} for score,d in found]

async def grounded_answer(topic,lang,query,verified_facts):
    try:docs_found=await semantic_retrieve(topic,lang,query) or retrieve_policy(topic,lang,query)
    except Exception:docs_found=retrieve_policy(topic,lang,query)
    # Stable primary citation for cashflow and dispute process, plus supporting
    # documents; never pretend vector embeddings are in use if not indexed.
    principal=next((d for d in docs_found if d['id']==('POL-FIN-001' if topic=='wellness' else 'POL-DIS-001')),docs_found[0])
    citation=f"[{principal['id']}, v{principal['version']}]"
    citations=[{'id':d['id'],'version':d['version'],'effective':d['effective'],
                'topic':d['topic'],'score':d['score']} for d in docs_found]
    if topic=='wellness':
        balance=Decimal(str(verified_facts['checking_balance_brl']))
        due=Decimal(str(verified_facts['upcoming_obligations_brl']))
        projected=balance-due
        if lang=='es':
            lead=('Sí, según los compromisos registrados.' if projected>=0 else
                  'No, los compromisos registrados superan tu saldo disponible.')
            answer=(f'{lead} Tienes {money(balance,"es")} en cuenta, y tus próximas obligaciones suman {money(due,"es")}. '
                    f'Te quedarían {money(projected,"es")} después de pagarlas. '
                    f'Es una estimación educativa, no una recomendación financiera. {citation}')
        else:
            lead=('Yes, based on the recorded commitments.' if projected>=0 else
                  'No, your recorded commitments exceed your checking balance.')
            answer=(f'{lead} Your checking balance is {money(balance,"en")}, with {money(due,"en")} in upcoming bills. '
                    f'That leaves {money(projected,"en")} after those bills. '
                    f'This is an educational estimate, not financial advice. {citation}')
    else:
        if lang=='es':
            answer=('Puedes registrar una reclamación por una compra con tarjeta que no reconoces. '
                    'El caso se revisa; no se garantiza un reembolso ni un plazo de resolución. '
                    'Bloquear la tarjeta es una acción independiente que exige tu autorización. ')
        else:
            answer=('You can dispute an unrecognized card transaction. '
                    'The case is reviewed; no refund or decision deadline is guaranteed. '
                    'Card blocking is a separate action requiring your approval. ')
        if verified_facts.get('merchant'):
            answer+=f"{verified_facts['merchant']} — {money(verified_facts['amount_brl'],lang)}. "
        answer+=citation
    return {'answer':answer,'document':principal,'documents':docs_found,
            'citations':citations,'retrieval_method':principal['retrieval_method']}
