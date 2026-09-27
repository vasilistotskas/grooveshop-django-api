import unittest

from core.utils.files import MAX_FILENAME_LENGTH, sanitize_filename

_RANDOM_STEM = r"[0-9a-f]{12}"


class TestSanitizeFilename(unittest.TestCase):
    def test_safe_names_are_unchanged(self):
        for name in (
            "document.txt",
            "My-File_Name.123.txt",
            "IMG_20231225_143052.jpg",
            "archive.tar.gz",
            "MyFile.TXT",
            "filename",
        ):
            with self.subTest(name=name):
                self.assertEqual(sanitize_filename(name), name)

    def test_unsafe_characters_are_removed(self):
        cases = [
            ("my document.txt", "mydocument.txt"),
            ("My Document (1).pdf", "MyDocument1.pdf"),
            ("file!!!???###.txt", "file.txt"),
            ("file\x00name.txt", "filename.txt"),
            ("file<script>.txt", "filescript.txt"),
            ("x.p!n@g", "x.png"),
        ]
        for given, expected in cases:
            with self.subTest(given=given):
                self.assertEqual(sanitize_filename(given), expected)

    def test_greek_is_transliterated_and_keeps_its_extension(self):
        """The regression: ``εικόνα.png`` used to be stored as ``png``."""
        self.assertEqual(sanitize_filename("εικόνα.png"), "eikona.png")

    def test_accents_are_folded(self):
        self.assertEqual(sanitize_filename("résumé.pdf"), "resume.pdf")

    def test_untransliterable_stem_gets_a_random_one(self):
        for name in ("файл.txt", "文件.txt", "😀.txt"):
            with self.subTest(name=name):
                self.assertRegex(
                    sanitize_filename(name), rf"^{_RANDOM_STEM}\.txt$"
                )

    def test_name_is_never_empty(self):
        for name in ("", ".", ".....", "@#$%", "   \t  "):
            with self.subTest(name=name):
                self.assertRegex(sanitize_filename(name), rf"^{_RANDOM_STEM}$")

    def test_directory_components_are_dropped(self):
        cases = [
            ("../../../etc/passwd", "passwd"),
            ("path/to/file.txt", "file.txt"),
            ("..\\..\\windows\\image.png", "image.png"),
        ]
        for given, expected in cases:
            with self.subTest(given=given):
                self.assertEqual(sanitize_filename(given), expected)

    def test_dots_are_collapsed_and_trimmed(self):
        cases = [
            ("file...name....txt", "file.name.txt"),
            ("...filename.txt", "filename.txt"),
        ]
        for given, expected in cases:
            with self.subTest(given=given):
                self.assertEqual(sanitize_filename(given), expected)

    def test_truncation_keeps_the_extension(self):
        result = sanitize_filename("a" * 60 + ".jpeg")
        self.assertEqual(len(result), MAX_FILENAME_LENGTH)
        self.assertTrue(result.endswith(".jpeg"))

    def test_idempotent(self):
        once = sanitize_filename("Στιγμιότυπο οθόνης.PNG")
        self.assertEqual(sanitize_filename(once), once)

    def test_non_string_input_raises(self):
        for value in (None, 123, ["file.txt"]):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, AttributeError)):
                    sanitize_filename(value)
