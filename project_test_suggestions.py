"""Nearby executed IDs for review; never aliases, matches, or passing evidence."""
from difflib import SequenceMatcher
import re
from urllib.parse import unquote

STOP = {'node','python','test','tests','case','when','then','is','are','the','a','an','for','from','with','this','in','on','to','and','as','it'}


def words(value):
    value=re.sub(r'([a-z0-9])([A-Z])',r'\1 \2',unquote(value[:1000]))
    return set(re.findall(r'[^\W_]+',value.casefold(),flags=re.UNICODE))-STOP


def nearby_tests(tests, report, query, limit=6):
    """Rank only IDs already certified by the fresh caller; select nothing."""
    query_words=words(query)
    if not query_words:return []
    identities={key:value for check in (report or {}).get('checks',[]) for key,value in (check.get('test_identities') or {}).items()}
    query_text=' '.join(sorted(query_words));ranked=[]
    for name in dict.fromkeys(tests):
        identity=identities.get(name,{})
        text=(identity.get('suite','')+' '+identity.get('name','')) if identity else name
        terms=words(text);shared=len(terms & query_words)
        if not shared:continue
        similarity=SequenceMatcher(None,query_text,' '.join(sorted(terms)),autojunk=False).ratio()
        overlap=2*shared/(len(terms)+len(query_words))
        if not (shared>=min(2,len(query_words)) and overlap>=0.4 or similarity>=0.75):continue
        # Negation distinguishes a rejection test from an acceptance test.
        negation=0.08 if ('not' in terms)==('not' in query_words) else -0.08
        ranked.append((0.65*overlap+0.35*similarity+negation,shared,name))
    ranked.sort(key=lambda row:(-row[0],-row[1],row[2]))
    return [row[2] for row in ranked[:limit]]


def zero_query_feedback(owner, report, tests, query, priority=()):
    run_ids=tuple(sorted(str(check.get('validation_run_id')) for check in (report or {}).get('checks',[]) if check.get('validation_run_id')))
    key=(getattr(owner,'key',None),(report or {}).get('fingerprint'),run_ids,' '.join(query.casefold().split()))
    state_owner=getattr(owner,'workspace',owner)
    seen=getattr(state_owner,'_zero_catalog_queries',{})
    count=seen.get(key,0)+1;seen[key]=count
    if len(seen)>128:seen.pop(next(iter(seen)))
    state_owner._zero_catalog_queries=seen
    nearby=nearby_tests(tests,report,query)
    priority_set=set(priority)
    original=[name for name in dict.fromkeys(tests) if name not in priority_set]
    examples=list(dict.fromkeys(original[:3]+original[-3:])) if not nearby else []
    return {'literal_match_count':0,'nearby_successful_tests':nearby,
            'other_actual_test_examples':examples,'examples_are_not_query_matches':True,
            'requires_relevance_review':True,'identical_zero_query_count':count,
            'action':'This exact query matched zero tests. Repeating it or changing its page cannot create a match. '
                     'Review the actually executed IDs suggested here, copy a relevant one verbatim into the affected plan rows, '
                     'and save the correction. Suggestions have not changed the plan or granted acceptance. '
                     'Do not rerun already passing unchanged code to fix a spelling. Broaden the query if none is relevant. '
                     'Discovery adapters and suite commands may define no test ID of their own. Full-suite execution '
                     'is independently mandatory; unranked examples are actual original test IDs, not matches to the query.'}
