from django import forms
from django.core.exceptions import ValidationError
import os


class ImageUploadForm(forms.Form):
    file = forms.ImageField()

    def clean_file(self):
        file = self.cleaned_data["file"]
        valid_extensions = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"]
        ext = os.path.splitext(file.name)[1].lower()
        if ext not in valid_extensions:
            raise ValidationError(
                f"Unsupported file extension. Supported formats: {', '.join(valid_extensions)}"
            )
        return file
