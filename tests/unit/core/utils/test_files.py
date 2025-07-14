import unittest

from core.utils.files import sanitize_filename


class TestSanitizeFilename(unittest.TestCase):
    def test_basic_filename(self):
        result = sanitize_filename("document.txt")
        self.assertEqual(result, "document.txt")

    def test_filename_with_spaces(self):
        result = sanitize_filename("my document.txt")
        self.assertEqual(result, "mydocument.txt")

    def test_filename_with_special_characters(self):
        result = sanitize_filename("file@#$%^&*()+={}[]|\\:;\"'<>?,/~`")
        self.assertEqual(result, "file")

    def test_filename_with_allowed_characters(self):
        result = sanitize_filename("My-File_Name.123.txt")
        self.assertEqual(result, "My-File_Name.123.txt")

    def test_filename_with_numbers(self):
        result = sanitize_filename("file123.txt")
        self.assertEqual(result, "file123.txt")

    def test_filename_with_uppercase_and_lowercase(self):
        result = sanitize_filename("MyFileName.TXT")
        self.assertEqual(result, "MyFileName.TXT")

    def test_filename_with_multiple_dots(self):
        result = sanitize_filename("file...name....txt")
        self.assertEqual(result, "file.name.txt")

    def test_filename_starting_with_dots(self):
        result = sanitize_filename("...filename.txt")
        self.assertEqual(result, "filename.txt")

    def test_filename_ending_with_dots(self):
        result = sanitize_filename("filename.txt...")
        self.assertEqual(result, "filename.txt")

    def test_filename_only_dots(self):
        result = sanitize_filename(".....")
        self.assertEqual(result, "")

    def test_empty_string(self):
        result = sanitize_filename("")
        self.assertEqual(result, "")

    def test_whitespace_only_string(self):
        result = sanitize_filename("   \t\n\r  ")
        self.assertEqual(result, "")

    def test_filename_length_under_limit(self):
        filename = "a" * 40 + ".txt"
        result = sanitize_filename(filename)
        self.assertEqual(result, filename)
        self.assertEqual(len(result), 44)

    def test_filename_length_exactly_at_limit(self):
        filename = "a" * 46 + ".txt"
        result = sanitize_filename(filename)
        self.assertEqual(result, filename)
        self.assertEqual(len(result), 50)

    def test_filename_length_over_limit(self):
        filename = "a" * 60 + ".txt"
        result = sanitize_filename(filename)
        self.assertEqual(len(result), 50)
        self.assertEqual(result, "a" * 50)

    def test_long_filename_with_extension(self):
        filename = (
            "very_long_filename_that_exceeds_the_fifty_character_limit.txt"
        )
        result = sanitize_filename(filename)
        self.assertEqual(len(result), 50)
        self.assertEqual(result, filename[:50])

    def test_unicode_characters(self):
        result = sanitize_filename("файл.txt")
        self.assertEqual(result, "txt")

    def test_unicode_emoji(self):
        result = sanitize_filename("file😀📁.txt")
        self.assertEqual(result, "file.txt")

    def test_chinese_characters(self):
        result = sanitize_filename("文件.txt")
        self.assertEqual(result, "txt")

    def test_accented_characters(self):
        result = sanitize_filename("résumé.pdf")
        self.assertEqual(result, "rsum.pdf")

    def test_filename_with_underscores(self):
        result = sanitize_filename("my_file_name.txt")
        self.assertEqual(result, "my_file_name.txt")

    def test_filename_with_hyphens(self):
        result = sanitize_filename("my-file-name.txt")
        self.assertEqual(result, "my-file-name.txt")

    def test_filename_mixed_separators(self):
        result = sanitize_filename("my-file_name.with-dots.txt")
        self.assertEqual(result, "my-file_name.with-dots.txt")

    def test_filename_only_extension(self):
        result = sanitize_filename(".txt")
        self.assertEqual(result, "txt")

    def test_filename_no_extension(self):
        result = sanitize_filename("filename")
        self.assertEqual(result, "filename")

    def test_filename_multiple_extensions(self):
        result = sanitize_filename("archive.tar.gz")
        self.assertEqual(result, "archive.tar.gz")

    def test_filename_with_path_separators(self):
        result = sanitize_filename("path/to/file.txt")
        self.assertEqual(result, "pathtofile.txt")

        result = sanitize_filename("path\\to\\file.txt")
        self.assertEqual(result, "pathtofile.txt")

    def test_filename_with_tabs_and_newlines(self):
        result = sanitize_filename("file\tname\n.txt")
        self.assertEqual(result, "filename.txt")

    def test_filename_with_null_bytes(self):
        result = sanitize_filename("file\x00name.txt")
        self.assertEqual(result, "filename.txt")

    def test_filename_with_control_characters(self):
        result = sanitize_filename("file\x01\x02\x03name.txt")
        self.assertEqual(result, "filename.txt")

    def test_complex_sanitization(self):
        result = sanitize_filename(
            "...My File@#$%^&*()Name...with spaces...and...dots....txt..."
        )
        self.assertEqual(result, "MyFileName.withspaces.and.dots.txt")

    def test_edge_case_only_special_characters(self):
        result = sanitize_filename("@#$%^&*()")
        self.assertEqual(result, "")

    def test_edge_case_only_dots_and_spaces(self):
        result = sanitize_filename("... ... ...")
        self.assertEqual(result, "")

    def test_preservation_of_case(self):
        result = sanitize_filename("MyFile.TXT")
        self.assertEqual(result, "MyFile.TXT")

    def test_numeric_filename(self):
        result = sanitize_filename("12345.678")
        self.assertEqual(result, "12345.678")

    def test_single_character_filename(self):
        result = sanitize_filename("a")
        self.assertEqual(result, "a")

    def test_single_dot_filename(self):
        result = sanitize_filename(".")
        self.assertEqual(result, "")

    def test_filename_with_repeated_special_chars(self):
        result = sanitize_filename("file!!!???###.txt")
        self.assertEqual(result, "file.txt")

    def test_filename_boundary_at_50_chars_with_dots(self):
        filename = "a" * 45 + "....." + ".txt"
        result = sanitize_filename(filename)
        expected = "a" * 45 + ".txt"
        self.assertEqual(result, expected)
        self.assertEqual(len(result), 49)

    def test_filename_with_dots_at_truncation_point(self):
        filename = "a" * 48 + "....txt"
        result = sanitize_filename(filename)
        self.assertEqual(len(result), 50)
        self.assertEqual(result, "a" * 48 + ".t")

    def test_real_world_filenames(self):
        test_cases = [
            ("My Document (1).pdf", "MyDocument1.pdf"),
            ("IMG_20231225_143052.jpg", "IMG_20231225_143052.jpg"),
            ("Report - Final Version.docx", "Report-FinalVersion.docx"),
            ("data_export_2023-12-25.csv", "data_export_2023-12-25.csv"),
            ("config.backup.json", "config.backup.json"),
            ("script.sh.bak", "script.sh.bak"),
        ]

        for input_filename, expected in test_cases:
            with self.subTest(filename=input_filename):
                result = sanitize_filename(input_filename)
                self.assertEqual(result, expected)

    def test_security_related_filenames(self):
        test_cases = [
            ("../../../etc/passwd", "etcpasswd"),
            ("..\\..\\windows\\system32", "windowssystem32"),
            (
                "file<script>alert('xss')</script>.txt",
                "filescriptalertxssscript.txt",
            ),
            ("file\x00.txt", "file.txt"),
            (
                "CON.txt",
                "CON.txt",
            ),
            ("file|pipe.txt", "filepipe.txt"),
        ]

        for input_filename, expected in test_cases:
            with self.subTest(filename=input_filename):
                result = sanitize_filename(input_filename)
                self.assertEqual(result, expected)

    def test_function_return_type(self):
        result = sanitize_filename("test.txt")
        self.assertIsInstance(result, str)

        result = sanitize_filename("")
        self.assertIsInstance(result, str)

        result = sanitize_filename("@#$%")
        self.assertIsInstance(result, str)

    def test_function_idempotency(self):
        filename = "My File@#$%^&*()Name.txt"
        result1 = sanitize_filename(filename)
        result2 = sanitize_filename(result1)
        result3 = sanitize_filename(result2)

        self.assertEqual(result1, result2)
        self.assertEqual(result2, result3)
        self.assertEqual(result1, "MyFileName.txt")

    def test_function_with_none_input(self):
        with self.assertRaises(TypeError):
            sanitize_filename(None)

    def test_function_with_integer_input(self):
        with self.assertRaises(TypeError):
            sanitize_filename(123)

    def test_function_with_list_input(self):
        with self.assertRaises(TypeError):
            sanitize_filename(["file.txt"])
