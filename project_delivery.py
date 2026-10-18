"""Check concrete file references in a delivery against the actual workspace."""
import re
from pathlib import PurePosixPath

FILE_TYPES={'.py','.pyw','.js','.mjs','.cjs','.html','.htm','.css','.json','.md','.txt','.toml','.yaml','.yml','.svg','.csv','.xml','.sql'}


def missing_delivery_files(workspace, text):
    missing=set()
    basenames=None
    for line in text.splitlines():
        if any(phrase in line.casefold() for phrase in ('not created','does not exist','not found','غير موجود','لم يُنشأ','لم يتم إنشاء','لم يتم انشاء')):
            continue
        references=re.findall(r'(?<!`)`([^`\n]+)`(?!`)',line)+re.findall(r'\[[^\]]+\]\(([^)]+)\)',line)
        for name in references:
            name=name.strip().strip('<>')
            if '://' in name or any(c in name for c in '*?\r\n') or name.startswith(('python ','node ','npm ','& ')):
                continue
            name=re.sub(r':\d+$','',name)
            if PurePosixPath(name.replace('\\','/')).suffix.lower() not in FILE_TYPES:
                continue
            try:exists=workspace.path(name).is_file()
            except ValueError:exists=False
            # A bare filename in prose may identify one nested deliverable.
            # Explicit paths must still resolve exactly inside the workspace.
            parsed=PurePosixPath(name.replace('\\','/'))
            if not exists and len(parsed.parts)==1 and not parsed.is_absolute() and ':' not in name:
                if basenames is None:
                    basenames={}
                    for item in workspace.files():
                        candidate=item['path']
                        try:valid=workspace.path(candidate).is_file()
                        except ValueError:valid=False
                        if valid:basenames.setdefault(PurePosixPath(candidate).name.casefold(),set()).add(candidate)
                exists=len(basenames.get(parsed.name.casefold(),()))==1
            if not exists:missing.add(name)
    return sorted(missing)
