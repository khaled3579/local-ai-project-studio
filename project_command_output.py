"""Bounded command views with durable complete output and visible failures."""
import hashlib
from pathlib import Path
import re
import uuid

LIMIT = 24000
DIAGNOSTIC = re.compile(r'^\s*(?:not ok\b|(?:FAIL|ERROR):|Traceback\b|[\w.]*?(?:Error|Exception)\b|error\s*(?::|TS\d+)|failureType:|✖|●)', re.I)


def excerpt(value, failed):
    if len(value) <= LIMIT:
        return value, {'truncated': False, 'diagnostic_blocks': 0, 'shown_blocks': 0}
    lines = value.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if DIAGNOSTIC.search(line)] if failed else []
    if not starts:
        return value[:11980]+'\n[output excerpt; full output saved]\n'+value[-11980:], {
            'truncated': True, 'diagnostic_blocks': 0, 'shown_blocks': 0}
    blocks=[];covered=-1
    for i in starts:
        if i <= covered:continue
        end=min(len(lines),i+35)
        before=''.join(lines[max(0,i-2):i])[-300:]
        blocks.append(before+''.join(lines[i:end]));covered=end-1
    selected=[];budget=17500
    for block in blocks:
        if budget <= 0:break
        part=block[:min(3500,budget)];selected.append(part);budget-=len(part)
    text=('[failure diagnostics; full output saved]\n'+'\n[diagnostic block]\n'.join(selected)+
          f'\n[shown {len(selected)} of {len(blocks)} diagnostic blocks]\n'+
          '[output beginning]\n'+value[:2000]+'\n[output end]\n'+value[-3500:])
    return text[:LIMIT], {'truncated': True, 'diagnostic_blocks': len(blocks), 'shown_blocks': len(selected)}


def command_result(workspace, argv, exit_code, stdout, stderr, seconds, timed_out=False):
    failed = exit_code != 0 or timed_out
    out, out_info=excerpt(stdout,failed);err, err_info=excerpt(stderr,failed)
    result={'argv':argv,'exit_code':exit_code,'stdout':out,'stderr':err,'seconds':seconds}
    if timed_out:result['timed_out']=True
    if failed or out_info['truncated'] or err_info['truncated']:
        base=workspace.meta/'command-output'
        if base.is_symlink() or not base.resolve().is_relative_to(workspace.meta.resolve()):
            raise ValueError('Command output storage must remain inside project metadata')
        directory=base/uuid.uuid4().hex
        try:
            directory.mkdir(parents=True,exist_ok=False)
            logs={}
            for name,value,info in [('stdout',stdout,out_info),('stderr',stderr,err_info)]:
                path=directory/(name+'.txt')
                with path.open('x',encoding='utf-8',newline='') as stream:stream.write(value)
                logs[name]={**info,'path':path.relative_to(workspace.root).as_posix(),
                    'characters':len(value),'sha256':hashlib.sha256(value.encode('utf-8')).hexdigest()}
            result['output_logs']=logs
        except OSError as error:
            # No successful result when the required audit could not be saved.
            raise OSError(f'Cannot save complete command output: {error}\n{out}\n{err}') from error
    return result
