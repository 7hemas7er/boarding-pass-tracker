"""The layering of the core (INTEGRATION_PLAN.md 2.1, 3.3 R-A, 7.1 T1): no module under custom_components/
boarding_pass_tracker/core imports Home Assistant (or aiohttp, voluptuous, paho, websocket-client, a barcode
library), starts a thread, sleeps, prints or changes the umask; its only dependency outside the standard library is
Pillow (in Home Assistant Core); every import of a sibling has the two branches of E1 (package and top level), and
the package form works. Read from the AST.

  python3 -I -B -m unittest discover -s tests/core -p test_layering.py -v
"""
import ast, importlib.util, os, subprocess, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402

CORE = support.CORE
FORBIDDEN = ("homeassistant", "aiohttp", "voluptuous", "probatio", "paho", "websocket", "zxingcpp", "pillow_heif",
             "requests", "httpx")
THIRD_PARTY = {"PIL"}
CALLS = {("threading", "Thread"), ("time", "sleep"), ("os", "umask"), ("os", "system"), ("subprocess", "run"),
         ("subprocess", "Popen"), ("importlib", "import_module")}


def modules():
    return sorted(f for f in os.listdir(CORE) if f.endswith(".py"))


def tree(name):
    with open(os.path.join(CORE, name), encoding="utf-8") as f:
        return ast.parse(f.read(), name)


def siblings():
    return {f[:-3] for f in modules() if f != "__init__.py"}


class Layering(unittest.TestCase):
    def test_no_home_assistant_import(self):
        for name in modules():
            for node in ast.walk(tree(name)):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                for n in names:
                    with self.subTest(module=name, imports=n):
                        self.assertNotIn(n.split(".")[0], FORBIDDEN)
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    with self.subTest(module=name, string=node.value[:40]):
                        self.assertFalse(node.value.startswith("homeassistant"), node.value)

    def test_only_the_standard_library_and_pillow(self):
        stdlib = set(sys.stdlib_module_names)
        for name in modules():
            for node in ast.walk(tree(name)):
                if isinstance(node, ast.Import):
                    top = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    top = [(node.module or "").split(".")[0]]
                else:
                    continue
                for t in top:
                    with self.subTest(module=name, imports=t):
                        self.assertIn(t, stdlib | THIRD_PARTY | siblings())

    def test_no_threads_sleeps_prints_or_umask(self):
        for name in modules():
            for node in ast.walk(tree(name)):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                with self.subTest(module=name, line=node.lineno):
                    if isinstance(f, ast.Name):
                        self.assertNotIn(f.id, ("print", "__import__", "exec", "eval"))
                    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                        self.assertNotIn((f.value.id, f.attr), CALLS)

    def test_every_sibling_import_has_both_branches(self):
        """E1: `if __package__: from . import x` / `else: import x`, the same names in both."""
        own = siblings()
        for name in modules():
            t = tree(name)
            imported = set()
            for node in ast.walk(t):
                if isinstance(node, ast.Import):
                    imported |= {a.name for a in node.names if a.name in own}
            for node in t.body:
                if isinstance(node, ast.If) and ast.unparse(node.test) == "__package__":
                    rel = {a.name for n in node.body if isinstance(n, ast.ImportFrom) and n.level == 1
                           for a in n.names}
                    top = {a.name for n in node.orelse if isinstance(n, ast.Import) for a in n.names}
                    with self.subTest(module=name):
                        self.assertEqual(rel, top)
                        self.assertLessEqual(rel, own)
                        imported -= top
            with self.subTest(module=name):
                self.assertEqual(imported, set(), "a sibling imported outside the E1 header")

    def test_the_package_form_imports(self):
        """The core as a package (as Home Assistant imports it): every module, through the relative branch, in a
        fresh interpreter without the core folder on sys.path."""
        code = ("import importlib.util, sys\n"
                f"spec = importlib.util.spec_from_file_location('bptcore', {os.path.join(CORE, '__init__.py')!r},"
                f" submodule_search_locations=[{CORE!r}])\n"
                "pkg = importlib.util.module_from_spec(spec); sys.modules['bptcore'] = pkg; spec.loader.exec_module(pkg)\n"
                "import bptcore.engine, bptcore.api, bptcore.barcodes\n"
                "assert bptcore.engine.flights is sys.modules['bptcore.flights']\n"
                "assert bptcore.engine.barcodes is bptcore.api.barcodes\n"
                "assert 'engine' not in sys.modules and 'flights' not in sys.modules\n"
                "assert not [m for m in sys.modules if m.split('.')[0] in %r]\n"
                "print('ok')\n" % (FORBIDDEN,))
        r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=60,
                           cwd=os.path.dirname(CORE))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok"), r.stderr[-2000:])
        self.assertIsNotNone(importlib.util.find_spec("engine"))           # and the top-level form for the tests


if __name__ == "__main__":
    unittest.main()
