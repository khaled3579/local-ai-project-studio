"""Native folder selection, isolated from the HTTP server thread."""
import sys
import tkinter as tk
from tkinter import filedialog
from pathlib import Path

app = tk.Tk()
app.title('اختيار مجلد نماذج الذكاء الاصطناعي')
width, height = min(1000, app.winfo_screenwidth()-80), min(650, app.winfo_screenheight()-80)
app.geometry(f'{width}x{height}+{(app.winfo_screenwidth()-width)//2}+{(app.winfo_screenheight()-height)//2}')
app.configure(background='#131d2b')
app.attributes('-topmost', True)
app.update()
initial = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
while not initial.is_dir() and initial != initial.parent:
    initial = initial.parent
selected = filedialog.askdirectory(parent=app, title='اختر المجلد الذي يحتوي على نماذج GGUF', initialdir=str(initial), mustexist=True)
app.destroy()
if selected:
    print(selected)
