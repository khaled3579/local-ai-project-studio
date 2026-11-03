"""Offline transport fixtures; controller, protocol selection and tools stay real."""
from contextlib import contextmanager, ExitStack
from unittest.mock import MagicMock, patch
import requests
import project_tool_protocol


def review_fixture(requirements):
    """Explicit known fixture specification; never used on user projects."""
    catalog=requirements.catalog()
    items=[]
    for row in catalog['source_requirements']:
        text=row['text']; boundary=text.find(' and ')
        spans=[(0,len(text))] if boundary<0 else [(0,boundary),(boundary,len(text))]
        items.append({'requirement_id':row['id'],'reviewed_atomic':True,
                      'segments':[{'start':a,'end':b,'evidence_kind':'behavior'} for a,b in spans]})
    requirements.review(expected_revision=catalog['revision'],items=items)


@contextmanager
def scripted_transport(directory, protocol, post):
    model = directory / ('gemma4-unit.gguf' if protocol == 'json' else 'qwen-unit.gguf')
    model.write_bytes(b'unit-test identity only; never loaded')
    manager = MagicMock()
    manager.json.return_value = {'status': 'ready', 'actual_context': 32768,
                                 'active': {'model': str(model)}}

    def accounting(url, **kwargs):
        if not url.endswith(('/apply-template', '/tokenize')):
            raise AssertionError('Unexpected session request: '+url)
        result = MagicMock()
        result.status_code = 200
        result.json.return_value = {'prompt': 'template'} if url.endswith('/apply-template') else {'tokens': [123]}
        result.__enter__.return_value = result
        return result

    with ExitStack() as stack:
        stack.enter_context(patch.object(requests, 'post', side_effect=post))
        stack.enter_context(patch.object(requests, 'get', return_value=manager))
        stack.enter_context(patch.object(requests.Session, 'post', side_effect=accounting))
        stack.enter_context(patch.object(project_tool_protocol, 'CACHE', directory/'protocols.json'))
        yield
