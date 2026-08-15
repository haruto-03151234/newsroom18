import unittest

from scripts.news_pipeline.text_utils import (
    canonical_url,
    clean_text,
    looks_japanese,
    title_similarity,
)


class TextUtilsTests(unittest.TestCase):
    def test_tracking_parameters_and_fragment_are_removed(self):
        value = canonical_url("HTTPS://Example.COM/news/?id=7&utm_source=x&fbclid=y#top")
        self.assertEqual(value, "https://example.com/news?id=7")

    def test_non_web_and_private_urls_are_rejected(self):
        for value in ("javascript:alert(1)", "http://127.0.0.1/a", "https://user:pass@example.com/a"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_url(value)

    def test_html_and_control_characters_are_removed(self):
        self.assertEqual(clean_text("<script>alert(1)</script><b>本文</b>\x00"), "alert(1) 本文")

    def test_japanese_title_variants_are_similar(self):
        similarity = title_similarity("政府、新制度を発表｜新聞A", "政府が新制度を発表 - 新聞B")
        self.assertGreater(similarity, 0.78)

    def test_language_detection(self):
        self.assertTrue(looks_japanese("政府が新制度を発表しました"))
        self.assertFalse(looks_japanese("Government announces a new policy"))


if __name__ == "__main__":
    unittest.main()

