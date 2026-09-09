from __future__ import annotations

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from unfold.forms import UserCreationForm

from core.generators import UserNameGenerator
from user.models import UserAccount


class UserAccountCreationForm(UserCreationForm):
    """Admin add-form that fills a blank username instead of storing NULL.

    ``UserAccountManager.create_user`` has always generated a handle
    from the email when none is supplied
    (``user/managers/account.py``), but the admin's add view builds a
    plain ModelForm and posts whatever is in the field — so the manager
    is bypassed and a blank field reaches the database as NULL.

    NULL is legal on this column, but it leaves
    ``UserAccount.get_short_name()`` returning ``None`` for that row,
    which Django renders as the literal string in a few admin surfaces.
    Generating the handle keeps admin-created accounts identical to
    signup-created ones.

    This is the other half of the blog-author problem. The field is
    optional and always was, but its help text read "Required.", so the
    site owner typed a person's display name into a machine handle and
    hit the validator's no-spaces rule. The help text is fixed on the
    model; this makes leaving it blank actually pleasant.
    """

    # Declared explicitly to replace Django's ``UsernameField``, whose
    # ``to_python`` does ``len(value)`` with no None guard. Django's own
    # ``User.username`` can never be null, so that is safe there — but
    # THIS column is ``null=True``, so submitting the field empty
    # crashed the admin add view with
    # ``TypeError: object of type 'NoneType' has no len()``.
    #
    # Which means "just leave it blank" was never actually available to
    # the operator: the help text said "Required.", and obeying it hit
    # the no-spaces validator while ignoring it hit a 500. Either way
    # you could not add a blog author.
    #
    # ``empty_value=""`` keeps a blank submission a string all the way
    # through ``clean_username`` below. The model's validator still runs
    # in ``_post_clean``, so a display name typed in here is still
    # rejected — deliberately: a handle with a space is not URL-safe.
    username = forms.CharField(
        label=_("Username"),
        required=False,
        empty_value="",
        max_length=settings.ACCOUNT_USERNAME_MAX_LENGTH,
        help_text=UserAccount._meta.get_field("username").help_text,
    )

    class Meta(UserCreationForm.Meta):
        # Django's ``UserCreationForm.Meta.model`` is ``auth.User``,
        # which is SWAPPED here — touching its manager raises
        # "Manager isn't available". The admin never hit that because
        # ``ModelAdmin.get_form`` runs the class through
        # ``modelform_factory(self.model, form=...)``, which replaces
        # the model behind the scenes. Declaring it makes the form
        # correct on its own terms rather than only inside the admin,
        # which is also what lets it be unit-tested directly.
        model = UserAccount
        fields = ("email", "username")

    def clean_username(self) -> str:
        """Generate a handle when the operator left the field empty.

        Done in ``clean_username`` rather than at save time so the
        generated value still passes through the form's uniqueness
        check — ``UserNameGenerator`` hashes the email into the handle,
        but a collision must surface as a form error, not an
        ``IntegrityError`` at commit.
        """
        username = (self.cleaned_data.get("username") or "").strip()
        if username:
            return username

        email = (self.cleaned_data.get("email") or "").strip()
        if not email:
            # No email to derive from — leave it empty and let the
            # email field's own "required" error be the one the
            # operator sees, rather than inventing a handle from
            # nothing.
            return ""

        return UserNameGenerator().generate_username(email)
