import ast
import unittest
from pathlib import Path

from fastapi.responses import PlainTextResponse

ROOT = Path(__file__).resolve().parents[1]

class PlainTextDeliveryTests(unittest.TestCase):
    def test_plain_text_response_body_and_content_type(self):
        key = "SAMPLE-KEY-ABC123"
        response = PlainTextResponse(key)
        self.assertEqual(response.body, key.encode("utf-8"))
        self.assertTrue(response.media_type == "text/plain")
        self.assertEqual(response.status_code, 200)

    def test_both_success_paths_return_raw_keys(self):
        source = (ROOT / "app/main.py").read_text()
        tree = ast.parse(source)
        fulfill = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "fulfill")
        plaintext_returns = [
            node for node in ast.walk(fulfill)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name) and node.value.func.id == "PlainTextResponse"
        ]
        # Fresh provider fulfilment and successful idempotent retry must both be plain text.
        self.assertEqual(len(plaintext_returns), 2)
        self.assertNotIn('JSONResponse({"deliver":', source)

if __name__ == "__main__":
    unittest.main()
