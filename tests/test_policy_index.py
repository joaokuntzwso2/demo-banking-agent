import json
from pathlib import Path
from agents.retrieval import build_index,retrieve_policy,INDEX,docs

def test_multilingual_policy_corpus_and_persistent_retrieval():
    build_index()
    corpus=docs()
    assert len(corpus)>=30
    assert {d['language'] for d in corpus}=={'en','es'}
    assert all(d['id'] and d['version'] and d.get('effective') and d.get('fictional') for d in corpus)
    assert INDEX.exists()
    for topic in ('wellness','dispute'):
        for language in ('en','es'):
            results=retrieve_policy(topic,language,'payment dispute forecast')
            assert len(results)==3
            assert all(d['topic']==topic and d['language']==language for d in results)
            assert all(d['retrieval_method']=='persistent_fts5_bm25' for d in results)
