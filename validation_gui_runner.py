"""Execute a Tkinter entry point once without blocking in mainloop."""
import argparse
import json
from pathlib import Path
import runpy
import sys
import tkinter as tk
import traceback


def readonly_contrast_issues(roots):
    issues=[]
    def walk(widget):
        yield widget
        for child in widget.winfo_children():yield from walk(child)
    def luminance(widget,color):
        rgb=[v/65535 for v in widget.winfo_rgb(color)]
        values=[v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4 for v in rgb]
        return sum(a*b for a,b in zip(values,(0.2126,0.7152,0.0722)))
    for root in roots:
        for widget in walk(root):
            if widget.winfo_class()!='Entry' or str(widget.cget('state'))!='readonly':continue
            fg=widget.cget('foreground');bg=widget.cget('readonlybackground') or widget.cget('background')
            a,b=sorted((luminance(widget,fg),luminance(widget,bg)))
            ratio=(b+0.05)/(a+0.05)
            if ratio<2.0:issues.append({'widget':str(widget),'foreground':fg,'readonlybackground':bg,'ratio':round(ratio,2)})
    return issues


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--entry',required=True);parser.add_argument('--report',required=True)
    parser.add_argument('--run-id',required=True)
    args=parser.parse_args();roots=[];original=tk.Tk.__init__
    def initialize(root,*a,**kw):
        original(root,*a,**kw);root.withdraw();roots.append(root)
    tk.Tk.__init__=initialize
    tk.Misc.mainloop=lambda *a,**kw:None
    tk.mainloop=lambda *a,**kw:None
    entry=Path(args.entry).resolve()
    sys.path.insert(0,str(entry.parent))
    result={'runner':'tkinter:startup','run_id':args.run_id,'entry':str(entry),'success':False,'windows':0}
    try:
        runpy.run_path(str(entry),run_name='__main__')
        for root in roots:root.update_idletasks()
        result.update(success=bool(roots),windows=len(roots))
        if not roots:result['error']='No Tk root was constructed by the entry point'
        issues=readonly_contrast_issues(roots)
        result['readonly_contrast_issues']=issues
        if issues:
            result.update(success=False,error='Readonly Entry text is difficult to read: set a contrasting foreground and readonlybackground. Details: '+json.dumps(issues))
    except BaseException:
        result['error']=traceback.format_exc()
    finally:
        for root in roots:
            try:root.destroy()
            except tk.TclError:pass
        Path(args.report).write_text(json.dumps(result),encoding='utf-8')
    return 0 if result['success'] else 1

if __name__=='__main__':raise SystemExit(main())
