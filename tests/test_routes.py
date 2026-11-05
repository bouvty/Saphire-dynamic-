import ast
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class TestRoutes(unittest.TestCase):
    def test_routes(self):
        tree=ast.parse((ROOT/"app/main.py").read_text())
        paths=[d.args[0].value for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) for d in n.decorator_list if isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.func.attr=="post" and d.args and isinstance(d.args[0],ast.Constant)]
        self.assertIn("/v2/resellers/order/members/online",paths)
        self.assertIn("/v2/resellers/order/members/offline",paths)
if __name__=="__main__":unittest.main()
