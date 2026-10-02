import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HELPERS = Path(__file__).parents[1] / "helpers"
sys.path.insert(0, str(HELPERS))


def load(name):
    spec = importlib.util.spec_from_file_location(f"video_edit_{name}", HELPERS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


brand_kit = load("brand_kit")
fetch = load("fetch")
platforms = load("platforms")


class ColourExtractionTests(unittest.TestCase):
    def test_hex_rgb_and_labeled(self):
        text = "Primary — Muse Blue  HEX 0071F3   RGB 0 113 243\nInk #0f0f0d (text)\nnot a colour: &#1234; #12345"
        found = brand_kit.colors_in_text(text)
        self.assertIn(("#0071F3", "Primary — Muse Blue HEX 0071F3 RGB 0 113 243"), found)
        self.assertEqual(sum(1 for h, _ in found if h == "#0071F3"), 1)   # hex + rgb on one line → one hit
        self.assertIn("#0F0F0D", [h for h, _ in found])
        self.assertNotIn("#123450", [h for h, _ in found])

    def test_contrast(self):
        self.assertAlmostEqual(brand_kit.contrast("#FFFFFF", "#000000"), 21.0, places=1)
        fg, ratio = brand_kit.text_on("#0071F3")
        self.assertEqual(fg, "#FFFFFF")
        self.assertGreaterEqual(ratio, 4.5)
        self.assertEqual(brand_kit.text_on("#CBE5FE")[0], "#000000")

    def test_normalize_colors(self):
        out = brand_kit.normalize_colors({"primary": "#abc", "text": {"hex": "0f0f0d", "name": "Ink"},
                                          "gradients": [{"stops": ["#000", "#fff"]}]})
        self.assertEqual(out["primary"]["hex"], "#AABBCC")
        self.assertEqual(out["text"], {"hex": "#0F0F0D", "name": "Ink"})
        self.assertNotIn("gradients", out)

    def test_dominant_colors_ignore_transparency(self):
        from PIL import Image
        im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        im.paste((0, 113, 243, 255), (0, 0, 50, 100))
        cols = brand_kit.dominant_colors(im)
        self.assertEqual(len(cols), 1)
        self.assertLess(sum(abs(a - b) for a, b in zip(brand_kit.hex_to_rgb(cols[0]["hex"]), (0, 113, 243))), 12)

    def test_knockout_and_trim(self):
        from PIL import Image
        im = Image.new("RGB", (60, 40), (255, 255, 255))
        im.paste((10, 10, 10), (20, 10, 40, 30))
        out = brand_kit.trim_alpha(brand_kit.knockout(im, "#FFFFFF"), pad=0)
        self.assertEqual(out.size, (20, 20))


class PlatformTests(unittest.TestCase):
    def test_scaled_draft(self):
        p = platforms.scaled(platforms.get_platform("reels"), 720, 1280)
        self.assertEqual(p["safe"], (40, 190, 640, 987))
        self.assertEqual(p["caption_y"], 907)

    def test_for_canvas(self):
        self.assertEqual(platforms.platform_for_canvas(1080, 1920), "vertical")
        self.assertEqual(platforms.platform_for_canvas(1920, 1080), "youtube")
        self.assertEqual(platforms.platform_for_canvas(1080, 1350), "feed45")
        self.assertEqual(platforms.platform_for_canvas(1080, 1080), "square")


class FetchTests(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(fetch.parse_version("2026.08.19\n"), (2026, 8, 19))
        self.assertEqual(fetch.parse_version("2025.12.08.1"), (2025, 12, 8, 1))
        self.assertLess(fetch.parse_version("2023.10.13"), fetch.MIN_YTDLP)

    def test_licences(self):
        self.assertEqual(fetch.classify_license({"license": "Creative Commons Attribution license (reuse allowed)"}), "cc-by")
        self.assertEqual(fetch.classify_license({"license": "by-nc-sa"}), "cc-restricted")
        self.assertEqual(fetch.classify_license({"license": "CC0"}), "cc0")
        self.assertEqual(fetch.classify_license({}), "unknown")
        self.assertEqual(fetch.classify_license({"license": "Standard License"}), "other")

    def test_sections(self):
        self.assertEqual(fetch.parse_section("1:05-1:20"), (65.0, 80.0))
        self.assertEqual(fetch.parse_section("00:01:05.5-00:01:20"), (65.5, 80.0))
        with self.assertRaises(ValueError):
            fetch.parse_section("10-5")

    def test_runtime_flags(self):
        with patch.object(fetch.shutil, "which", side_effect=lambda b: None if b == "deno" else "/x/" + b), \
                patch.object(fetch, "_node_major", return_value=22):
            self.assertEqual(fetch.runtime_flags((2026, 8, 19), "uvx"), ["--js-runtimes", "node"])
            self.assertEqual(fetch.runtime_flags((2026, 8, 19), "system"),
                             ["--js-runtimes", "node", "--remote-components", "ejs:github"])
            self.assertEqual(fetch.runtime_flags((2023, 10, 13), "system-outdated"), [])

    def test_slugify(self):
        self.assertEqual(fetch.slugify("Atlanta skyline at dusk 🌆 — No Copyright!"), "atlanta-skyline-at-dusk-no-copyright")


if __name__ == "__main__":
    unittest.main()
