"""Model template accounting, separate from HTTP/UI orchestration."""
import logging
import time

import requests


LOG = logging.getLogger(__name__)
ACCOUNTING_ROUTES = frozenset({'/apply-template', '/tokenize'})
TRANSIENT_STATUS = frozenset({429, 502, 503, 504})


def accounting_post(session, model_url, route, payload, *, headers=None):
    """Retry only pure accounting endpoints, never model generation or tools."""
    if route not in ACCOUNTING_ROUTES:
        raise ValueError('Retries are restricted to model token accounting')
    for attempt in range(3):
        try:
            with session.post(model_url.rstrip('/') + route, json=payload,
                              headers=headers, timeout=(5, 20),
                              allow_redirects=False) as response:
                if response.status_code in TRANSIENT_STATUS and attempt < 2:
                    LOG.warning('Token accounting %s returned HTTP %s; retry %s/3',
                                route, response.status_code, attempt + 2)
                else:
                    response.raise_for_status()
                    # Invalid JSON/schema is not a transient connection failure.
                    return response.json()
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError) as error:
            if attempt == 2:
                raise
            LOG.warning('Token accounting %s failed (%s); retry %s/3',
                        route, type(error).__name__, attempt + 2)
        time.sleep((.5, 1.5)[attempt])
    raise AssertionError('unreachable accounting retry state')


def count_messages(model_url, messages, tools):
    text_messages, images = [], 0
    for message in messages:
        copy = dict(message)
        if isinstance(copy.get('content'), list):
            parts = []
            for part in copy['content']:
                if part.get('type') == 'image_url':
                    images += 1
                    parts.append({'type': 'text', 'text': '[visual attachment]'})
                else:
                    parts.append(part)
            copy['content'] = parts
        text_messages.append(copy)
    with requests.Session() as session:
        session.trust_env = False  # Local accounting must not use an OS proxy.
        applied = accounting_post(session, model_url, '/apply-template', {
            'messages': text_messages, 'tools': tools, 'reasoning_effort': 'none',
            'chat_template_kwargs': {'enable_thinking': False}},
            headers={'X-Studio-Accounting': '1'})
        encoded = accounting_post(session, model_url, '/tokenize',
                                  {'content': applied['prompt']})
    # Vision remains a conservative estimate; runtime usage receipts are authoritative.
    return len(encoded['tokens']) + images * 2048
