from pathlib import Path
import re
import unittest

from config import ENV_FILE, PROJECT_ROOT, Settings


class ConfigContractTests(unittest.TestCase):
    def test_env_file_is_the_project_root_env(self):
        expected_root = Path(__file__).resolve().parent.parent
        self.assertEqual(PROJECT_ROOT, expected_root)
        self.assertEqual(ENV_FILE, expected_root / ".env")
        self.assertTrue(Path(Settings.model_config["env_file"]).is_absolute())

    def test_example_covers_supported_optional_runtime_settings(self):
        content = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        keys = set(re.findall(r"(?m)^\s*([A-Z][A-Z0-9_]*)\s*=", content))
        expected = {
            "LLM_API_KEY",
            "LLM_VISION_PROVIDER",
            "LLM_VISION_BASE_URL",
            "LLM_VISION_API_KEY",
            "LLM_VISION_MODEL",
            "LLM_OCR_PROVIDER",
            "LLM_OCR_BASE_URL",
            "LLM_OCR_API_KEY",
            "LLM_OCR_MODEL",
            "DEEP_RESEARCH_MAX_ITEMS",
            "TENCENT_OCR_CONCURRENCY",
            "TENCENT_OCR_RATE_PER_SEC",
            "BAIDU_OCR_CONCURRENCY",
            "BAIDU_OCR_RATE_PER_SEC",
            "VISION_OCR_CONCURRENCY",
        }
        self.assertFalse(expected - keys, f"示例缺少变量：{sorted(expected - keys)}")
        self.assertNotIn("DEEP_RESEARCH_ENABLED", keys)
        self.assertNotIn("DEEP_RESEARCH_STRATEGY", keys)
        self.assertNotIn("LLM_FAST_ENABLE_THINKING", keys)
        self.assertNotIn("MATCH_QUICK_TOP_K", keys)

    def test_ci_uses_the_project_root_env_template(self):
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("cp ../.env.example ../.env", workflow)
        self.assertNotIn("cp .env.example .env", workflow)

    def test_project_text_files_do_not_contain_mojibake(self):
        roots = [
            PROJECT_ROOT / "README.md",
            PROJECT_ROOT / "docs",
            PROJECT_ROOT / "server",
            PROJECT_ROOT / "app" / "src",
            PROJECT_ROOT / ".github",
        ]
        mojibake_markers = (
            "\u951b",
            "\u9225",
            "\u9983",
            "\u59f9\u50b1\u4eb4",
            "\u5bb8\u6904\u7d8d\u4f4d",
        )
        offenders: list[str] = []
        for root in roots:
            paths = [root] if root.is_file() else root.rglob("*")
            for path in paths:
                if not path.is_file() or path.suffix not in {
                    ".md",
                    ".py",
                    ".ts",
                    ".vue",
                    ".yml",
                    ".yaml",
                }:
                    continue
                if any(part in {".venv", "node_modules", "dist", "__pycache__"} for part in path.parts):
                    continue
                content = path.read_text(encoding="utf-8")
                if any(marker in content for marker in mojibake_markers):
                    offenders.append(str(path.relative_to(PROJECT_ROOT)))
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
