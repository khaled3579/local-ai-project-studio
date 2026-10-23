"""Model template accounting, separate from HTTP/UI orchestration."""
import requests


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
    applied = requests.post(model_url + '/apply-template', json={
        'messages': text_messages, 'tools': tools, 'reasoning_effort': 'none',
        'chat_template_kwargs': {'enable_thinking': False}}, timeout=20)
    applied.raise_for_status()
    encoded = requests.post(model_url + '/tokenize', json={'content': applied.json()['prompt']}, timeout=20)
    encoded.raise_for_status()
    # Vision remains a conservative estimate; runtime usage receipts are authoritative.
    return len(encoded.json()['tokens']) + images * 2048
