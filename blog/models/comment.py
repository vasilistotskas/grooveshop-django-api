from core.models import TimeStampMixinModel
from core.models import UUIDModel
from django.db import models


class BlogComment(TimeStampMixinModel, UUIDModel):
    id = models.AutoField(primary_key=True)
    content = models.TextField(max_length=1000)
    is_approved = models.BooleanField(default=False)
    likes = models.ManyToManyField(
        "user.UserAccount", related_name="blog_comment_likes", blank=True
    )
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="blog_comment_user",
        on_delete=models.SET_NULL,
        null=True,
    )
    post = models.ForeignKey(
        "blog.BlogPost",
        related_name="blog_comment_post",
        on_delete=models.SET_NULL,
        null=True,
    )

    class Meta:
        unique_together = (("user", "post"),)
        ordering = ["-created_at"]

    def __str__(self):
        if len(self.content) > 50:
            comment: str = self.content[:50] + "..."
        else:
            comment = self.content
        return comment

    @property
    def number_of_likes(self) -> int:
        return self.likes.count()
