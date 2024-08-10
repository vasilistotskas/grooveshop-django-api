from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from settings import load_dotenv_file


class TestConfigBase(TestCase):
    @patch("os.path.isfile")
    @patch("dotenv.load_dotenv")
    def test_loads_dotenv_file_when_file_exists(self, mock_load_dotenv, mock_isfile):
        dotenv_file = Path(".env").resolve()
        mock_isfile.return_value = True

        load_dotenv_file()

        mock_isfile.assert_called_once_with(dotenv_file)
        mock_load_dotenv.assert_called_once_with(dotenv_file)
