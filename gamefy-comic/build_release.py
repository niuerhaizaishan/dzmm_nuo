"""Build one self-contained Gamefy entry without demo images or credentials."""
import argparse
import base64
from pathlib import Path
import re
from urllib.parse import urlsplit


def build(api_base, output):
    parts = urlsplit(api_base)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Use the HTTPS gallery API base without credentials or query parameters")
    root = Path(__file__).resolve().parent / "publish"
    html = (root / "index.html").read_text(encoding="utf-8")
    font = base64.b64encode((root / "assets/Phosphor.woff2").read_bytes()).decode("ascii")
    icons = (root / "assets/icons.css").read_text(encoding="utf-8")
    icons = re.sub(r"@font-face\s*\{.*?\}",
        '@font-face{font-family:"Phosphor";src:url(data:font/woff2;base64,' + font + ') format("woff2");font-weight:normal;font-style:normal;font-display:block;}',
        icons, count=1, flags=re.S)
    for href, css in (("assets/icons.css", icons), ("style.css", (root / "style.css").read_text(encoding="utf-8"))):
        html = html.replace('<link rel="stylesheet" href="' + href + '">', "<style>" + css + "</style>")
    import json
    html = html.replace('<script src="config.js"></script>',
        "<script>window.GALLERY_API_BASE=" + json.dumps(api_base.rstrip("/")) + ";</script>")
    app = (root / "app.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    html = html.replace('<script src="app.js"></script>', "<script>" + app + "</script>")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(build(args.api_base, args.output))
