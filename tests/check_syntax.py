"""Compile Python into temporary storage and validate every browser script."""
from html.parser import HTMLParser
from pathlib import Path
import py_compile
import shutil
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parents[1]


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inline = []
        self.external = []
        self.current = None
        self.module = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag != "script":
            return
        if attributes.get("src"):
            self.external.append(attributes["src"])
        else:
            self.current = []
            self.module = attributes.get("type") == "module"

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.inline.append(("".join(self.current), self.module))
            self.current = None


def main():
    python_files = [*PROJECT.joinpath("panel").rglob("*.py"),
                    *PROJECT.joinpath("server").glob("*.py"),
                    *PROJECT.joinpath("tests").rglob("*.py"),
                    *PROJECT.joinpath("tools").glob("*.py")]
    with tempfile.TemporaryDirectory(prefix="qq-panel-compile-") as folder:
        for index, path in enumerate(python_files):
            py_compile.compile(str(path), cfile=str(Path(folder) / ("%d.pyc" % index)), doraise=True)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("脚本语法检查需要 Node.js")
    for root in ("server", "tools", "tests", "panel/web"):
        for path in PROJECT.joinpath(root).rglob("*"):
            if path.suffix in (".js", ".mjs", ".cjs"):
                subprocess.run([node, "--check", str(path)], check=True)
    scripts = Scripts()
    scripts.feed(PROJECT.joinpath("panel/web/index.html").read_text(encoding="utf-8"))
    assert len(scripts.inline) >= 1 and len(scripts.external) >= 2, "界面脚本结构不完整"
    for path in scripts.external:
        assert PROJECT.joinpath("panel/web", path).is_file(), path
    for text, module in scripts.inline:
        subprocess.run([node, "--check", "--input-type=module" if module else "--input-type=commonjs"],
                       input=text, text=True, encoding="utf-8", check=True)
    print("PYTHON_COMPILE=PASS files=%d" % len(python_files))
    print("BROWSER_SYNTAX=PASS inline=%d external=%d" % (len(scripts.inline), len(scripts.external)))


if __name__ == "__main__":
    main()
