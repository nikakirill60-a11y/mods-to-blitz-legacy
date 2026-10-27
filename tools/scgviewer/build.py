"""Build the single-file viewer: src/*.{html,css,js} -> scgviewer.html"""
import os
here = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(here, "src")
read = lambda n: open(os.path.join(src, n), encoding="utf-8").read()
html = read("template.html")
html = html.replace("/*@@STYLE@@*/", read("style.css"))
html = html.replace("/*@@CORE@@*/", read("core.js").replace("</script", "<\\/script"))
html = html.replace("/*@@VIEWER@@*/", read("viewer.js").replace("</script", "<\\/script"))
out = os.path.join(here, "scgviewer.html")
with open(out, "w", encoding="utf-8", newline="\n") as f:
    f.write(html)
print("wrote", out, len(html), "bytes")
