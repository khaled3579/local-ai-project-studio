"""Conservative rejection of redundant permission questions during authorized work."""
import re
import unicodedata


def routine_permission(question):
    if not isinstance(question,str):
        return False
    text=''.join(character for character in unicodedata.normalize('NFKD',question.casefold())
                 if unicodedata.category(character)!='Mn')
    # These can be genuine missing decisions or external access; preserve them.
    if re.search(r'مزود|بواب[ةه].{0,8}دفع|مفتاح|كلم[ةه] المرور|حساب|نشر|دومين|stripe|provider|credential|password|secret|account|deploy|publish|domain',text):
        return False
    return bool(re.search(r'هل\s+(?:يمكنني|استطيع|تسمح).{0,70}(?:المضي|متابع[ةه] العمل|الاستمرار|البدء|كتاب[ةه] التغييرات|تعديل (?:الملفات|المشروع)|اصلاح)',text)
        or re.search(r'\b(?:may|can|should) i\s+(?:proceed|continue|start|write|edit|fix|read|run tests)\b',text))
