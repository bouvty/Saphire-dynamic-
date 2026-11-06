import ast
import unittest
from pathlib import Path

from fastapi.responses import PlainTextResponse
from app.main import format_delivery_message

ROOT = Path(__file__).resolve().parents[1]

class PlainTextDeliveryTests(unittest.TestCase):
    def test_delivery_includes_instructions_and_key(self):
        key = "SAMPLE-KEY-ABC123"
        expected = (
            "Thank you for using saphire ❤️\n"
            "please use our redeem panel below to redeem your key, follow the steps provided.\n"
            "https://memberix.live/redeem     KEY: SAMPLE-KEY-ABC123"
        )
        response = PlainTextResponse(format_delivery_message(key))
        self.assertEqual(response.body, expected.encode("utf-8"))
        self.assertEqual(response.media_type, "text/plain")
        self.assertEqual(response.status_code, 200)

    def test_success_paths_format_deliveries(self):
        source = (ROOT / "app/main.py").read_text()
        tree = ast.parse(source)
        fulfill = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "fulfill")
        responses = [
            node for node in ast.walk(fulfill)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name) and node.value.func.id == "PlainTextResponse"
        ]
        self.assertEqual(len(responses), 2)
        self.assertTrue(all(isinstance(node.value.args[0], ast.Call)
                            and getattr(node.value.args[0].func, "id", None) == "format_delivery_message"
                            for node in responses))
        self.assertNotIn('JSONResponse({"deliver":', source)

if __name__ == "__main__":
    unittest.main()
