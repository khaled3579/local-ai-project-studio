"""Small, read-only model facts and actual decode timing from llama-server logs."""
import math
import re
import time
from pathlib import Path
import csv


def expert_spec(info):
    meta=info.get('metadata') or {};arch=info.get('architecture','')
    total=meta.get(arch+'.expert_count')
    if total is None and arch and meta.get('general.architecture')==arch:
        total=0  # GGUF loader defaults missing expert_count to zero (dense).
    default=meta.get(arch+'.expert_used_count')
    total=total if type(total) is int and total>=0 else None
    scalar=type(default) is int and total is not None and 0<default<=total
    maximum=total
    groups,used=meta.get(arch+'.expert_group_count',0),meta.get(arch+'.expert_group_used_count',0)
    if scalar and type(groups) is int and groups>1:
        if type(used) is not int or not 0<used<groups or total%groups:scalar=False
        else:maximum=total//groups*used
    return {'total':total,'default':default if type(default) is int else (0 if total==0 else None),
            'supported':bool(total and scalar),'maximum':maximum if scalar else None,
            'key':arch+'.expert_used_count' if scalar else None,
            'reason':'نموذج كثيف لا يستخدم خبراء MoE' if total==0 else 'عدد الخبراء متغير حسب الطبقة أو بيانات التوجيه غير كافية' if not scalar else ''}


def expert_override(info, requested):
    if requested is None:return []
    spec=expert_spec(info)
    if not spec['supported']:raise ValueError('هذا النموذج لا يدعم التحكم بعدد خبراء MoE النشطين: '+spec['reason'])
    if type(requested) is not int or not 1<=requested<=spec['maximum']:
        raise ValueError('عدد الخبراء النشطين يجب أن يكون عددًا صحيحًا بين 1 و'+str(spec['maximum']))
    return ['--override-kv',spec['key']+'=int:'+str(requested)]


def parse_gpus(output):
    def number(value):
        try:
            result=float(value.strip())
            return result if math.isfinite(result) and result>=0 else None
        except ValueError:return None
    result=[]
    for row in csv.reader(output.splitlines(),skipinitialspace=True):
        if len(row)!=6:continue
        index=number(row[0])
        if index is None:continue
        result.append({'index':int(index),'name':row[1].strip(),'memory_mib':number(row[2]),
            'memory_total_mib':number(row[3]),'temperature_c':number(row[4]),'util_percent':number(row[5])})
    return result


def details(info, path):
    metadata=info.get('metadata') or {}
    arch=info.get('architecture','')
    def integer(key):
        value=metadata.get(arch+'.'+key)
        return value if type(value) is int and value>=0 else None
    size=info.get('bytes')
    if size is None:
        file=Path(path)
        split=re.match(r'^(.*)-(\d{5})-of-(\d{5})\.gguf$',file.name,re.I)
        parts=([file.with_name(f'{split[1]}-{i:05d}-of-{split[3]}.gguf') for i in range(1,int(split[3])+1)]
               if split and int(split[3])<=1024 else [file])
        try:size=sum(p.stat().st_size for p in parts)
        except OSError:size=None
    spec=expert_spec(info)
    return {'name':info.get('name') or Path(path).stem,'filename':Path(path).name,
            'bytes':size,'parameters':info.get('parameters_in_file'),'architecture':arch,
            'mtp_supported':info.get('mtp'),'experts_total':spec['total'],
            'experts_active':spec['default'],'expert_control':spec,'declared_context':info.get('declared_context')}


class DecodeTiming:
    def __init__(self):
        self.pending=''
        self.experts_used=None
        self.reset()

    def reset(self):
        self.speed=None
        self.phase='waiting'
        self.updated=None

    def feed(self, block):
        self.pending+=block.decode('utf-8',errors='replace') if isinstance(block,bytes) else block
        lines=self.pending.split('\n')
        self.pending=lines.pop()[-65536:]
        for line in lines:
            actual=re.search(r'\bn_expert_used\s*=\s*(\d+)',line)
            if actual:self.experts_used=int(actual[1])
            if 'new prompt,' in line:
                self.reset()
            match=re.search(r'\bn_gen\s*=\s*\d+,\s*tg\s*=\s*([\d.]+)\s*t/s',line)
            phase='live'
            if not match and 'prompt eval time' not in line:
                match=re.search(r'\beval time\s*=.*?([\d.]+)\s+tokens per second',line)
                phase='last'
            if match:
                value=float(match[1])
                if math.isfinite(value) and value>0:
                    self.speed,self.phase,self.updated=value,phase,time.time()
            if 'stop processing:' in line and self.speed is not None:
                self.phase='last'

    def snapshot(self):
        return {'tokens_per_second':self.speed,'phase':self.phase,'updated':self.updated,'source':'llama-server decode timings'}
