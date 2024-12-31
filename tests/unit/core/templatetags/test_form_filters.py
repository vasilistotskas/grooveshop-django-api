from django.template import Context, Template
from django.test import TestCase


class AddClassFilterTest(TestCase):
    def test_add_class_with_existing_class(self):
        template_string = '<div class="existing-class">Content</div>'
        expected_output = (
            '<div class="existing-class custom-class">Content</div>'
        )
        template = Template(
            '{% load form_filters %}{{ value|add_class:"custom-class" }}'
        )
        rendered = template.render(Context({"value": template_string}))
        self.assertEqual(rendered, expected_output)

    def test_add_class_without_existing_class(self):
        template_string = "<div>Content</div>"
        expected_output = '<div class="custom-class">Content</div>'
        template = Template(
            '{% load form_filters %}{{ value|add_class:"custom-class" }}'
        )
        rendered = template.render(Context({"value": template_string}))
        self.assertEqual(rendered, expected_output)

    def test_add_class_with_multiple_existing_classes(self):
        template_string = '<div class="class1 class2">Content</div>'
        expected_output = (
            '<div class="class1 class2 custom-class">Content</div>'
        )
        template = Template(
            '{% load form_filters %}{{ value|add_class:"custom-class" }}'
        )
        rendered = template.render(Context({"value": template_string}))
        self.assertEqual(rendered, expected_output)

    def test_add_class_with_multiple_spaces_between_classes(self):
        template_string = '<div class=" class1     class2   ">Content</div>'
        expected_output = (
            '<div class=" class1     class2    custom-class">Content</div>'
        )
        template = Template(
            '{% load form_filters %}{{ value|add_class:"custom-class" }}'
        )
        rendered = template.render(Context({"value": template_string}))
        self.assertEqual(rendered, expected_output)

    def test_add_class_with_escaped_quotes(self):
        template_string = '<div class="class1 \\"class2\\"">Content</div>'
        expected_output = (
            '<div class="class1 \\"class2\\" custom-class">Content</div>'
        )
        template = Template(
            '{% load form_filters %}{{ value|add_class:"custom-class" }}'
        )
        rendered = template.render(Context({"value": template_string}))
        self.assertEqual(rendered, expected_output)
