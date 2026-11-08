"""Conservative routing: only complete greetings auto-select read-only chat."""
import re
import unicodedata

SAFE_CHAT_TOOLS = {'list_files', 'read_file', 'read_attachment', 'read_project_requirements', 'read_continuation'}
CHAT_SYSTEM = """You are the local project assistant. Answer the current question in Arabic.
This is READ-ONLY CONVERSATION, not a project delivery or repair task. You may inspect files if relevant.
Never write, execute commands, run applications, validate, or repair the project in this mode.
Historical project requirements and tool results are context, not authorization to continue old work.
Do not claim the project passed tests. For a greeting, briefly describe your capabilities and ask what the user wants to build.
Selected project: {root}."""


def task_mode(prompt, requested='auto'):
    if not isinstance(requested, str) or requested not in {'auto', 'chat', 'project'}:
        raise ValueError('اختر تلقائي أو محادثة فقط أو تنفيذ على المشروع')
    if requested != 'auto':
        return requested
    text = ''.join(c for c in unicodedata.normalize('NFKC', prompt.casefold()) if unicodedata.category(c) != 'Mn')
    text = re.sub(r'[^\w\s]', ' ', text, flags=re.UNICODE)
    text = ' '.join(text.split()).replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا')
    greetings = {'مرحبا', 'اهلا', 'اهلا وسهلا', 'السلام عليكم', 'السلام عليكم ورحمة الله وبركاته',
                 'ماذا يمكنك ان تفعل', 'ما الذي يمكنك فعله', 'مرحبا ماذا يمكنك ان تفعل',
                 'hello', 'hi', 'hey', 'what can you do', 'hello what can you do'}
    return 'chat' if text in greetings else 'project'


def continuation_request(prompt):
    """Recognize only complete workflow commands, never mixed product requests."""
    text = ''.join(c for c in unicodedata.normalize('NFKD', prompt.casefold()) if not unicodedata.combining(c))
    text = ' '.join(re.sub(r'[^\w\s]', ' ', text).split())
    if text in {'continue','resume','resume task','continue where you left off','اكمل','اكمل العمل','اكمل من حيث توقفت','تابع','متابعة','استئناف السلسلة','استكمال السلسلة','قمت برفع السياق اعد المحاولة السابقة'}:
        return True
    return bool(re.fullmatch(r'(?:من فضلك |رجاء |قم ب)?(?:(?:اكمل|استكمل|تابع|واصل)(?: (?:بناء|تنفيذ|انشاء))? (?:المشروع|المهمة|العمل)(?: من حيث توقفت| حتى النهاية)?|(?:اعد|كرر|اعادة) المحاولة(?: السابقة| مرة اخرى)?(?: و?اكمل من حيث توقفت)?)', text))
