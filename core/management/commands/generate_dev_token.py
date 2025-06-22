import json
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from knox.models import AuthToken
from knox.settings import knox_settings

User = get_user_model()


@dataclass
class TokenContext:
    token: str
    user: User
    created: str
    digest: str
    expiry_str: str
    expires_in: float | None
    output_path: str


class Command(BaseCommand):
    help = "Generate a Knox token for development"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username", type=str, help="Username for token generation"
        )
        parser.add_argument(
            "--email", type=str, help="Email for token generation"
        )
        parser.add_argument(
            "--output",
            type=str,
            default=".auth-token",
            help="Output file for token (default: .auth-token)",
        )
        parser.add_argument(
            "--format",
            type=str,
            choices=["plain", "json", "env"],
            default="plain",
            help="Output format: plain, json, or env",
        )
        parser.add_argument(
            "--ttl",
            type=int,
            default=24 * 30,
            help="Token TTL in hours; overrides default Knox settings",
        )

    def handle(self, *args, **options):
        try:
            user = self._get_user(options)
        except User.DoesNotExist:
            self.stderr.write(self.style.ERROR("User not found"))
            return
        instance, token = self._create_token(user, options["ttl"])
        expiry_str, _, expires_in = self._compute_expiry(instance)
        ctx = TokenContext(
            token=token,
            user=user,
            created=instance.created.isoformat(),
            digest=instance.digest,
            expiry_str=expiry_str,
            expires_in=expires_in,
            output_path=options["output"],
        )
        self._write_token_file(ctx, options["format"])
        self._print_summary(
            user,
            token,
            expiry_str,
            expires_in,
            ctx.output_path,
            options["format"],
        )

    def _get_user(self, opts):
        if not opts.get("username") and not opts.get("email"):
            raise User.DoesNotExist
        lookup = (
            {"username": opts.get("username")}
            if opts.get("username")
            else {"email": opts.get("email")}
        )
        return User.objects.get(**lookup)

    def _create_token(
        self, user: User, ttl_hours: int | None
    ) -> tuple[AuthToken, str]:
        delta = timedelta(hours=ttl_hours) if ttl_hours else None
        return AuthToken.objects.create(user=user, expiry=delta)

    def _compute_expiry(
        self, instance: AuthToken
    ) -> tuple[str, str | None, float | None]:
        if instance.expiry:
            expiry_time = instance.expiry
        elif knox_settings.TOKEN_TTL:
            expiry_time = instance.created + knox_settings.TOKEN_TTL
        else:
            return "Never", None, None

        local_expiry = timezone.localtime(expiry_time)
        expiry_str = local_expiry.strftime("%Y-%m-%d %H:%M:%S %Z")

        expires_in = (expiry_time - timezone.now()).total_seconds()
        return expiry_str, local_expiry.isoformat(), expires_in

    def _write_token_file(self, ctx: TokenContext, fmt: str) -> None:
        if fmt == "plain":
            with open(ctx.output_path, "w") as fp:
                fp.write(ctx.token)
        elif fmt == "json":
            payload = {
                "token": ctx.token,
                "user": {
                    "id": ctx.user.id,
                    "username": ctx.user.username,
                    "email": ctx.user.email,
                },
                "created": ctx.created,
                "expiry": None if ctx.expiry_str == "Never" else ctx.expiry_str,
                "expires_in_seconds": ctx.expires_in,
                "digest": ctx.digest,
            }
            with open(ctx.output_path, "w") as fp:
                json.dump(payload, fp, indent=2)
        elif fmt == "env":
            with open(ctx.output_path, "w") as fp:
                fp.write(
                    f"# Generated on {timezone.localtime():%Y-%m-%d %H:%M:%S %Z}\n"
                    f"# User: {ctx.user.username} ({ctx.user.email})\n"
                    f"# Expires: {ctx.expiry_str}\n"
                    f"AUTH_TOKEN={ctx.token}\n"
                )

    def _print_summary(
        self,
        user: User,
        token: str,
        expiry_str: str,
        expires_in: float | None,
        out_path: str,
        fmt: str,
    ) -> None:
        self.stdout.write(
            self.style.SUCCESS(f"✅ Token generated for {user.username}")
        )
        self.stdout.write(f"📄 Token saved to: {out_path} (format: {fmt})")
        self.stdout.write(f"🔑 Token: {token[:20]}...")
        if expiry_str == "Never":
            self.stdout.write(self.style.SUCCESS("♾️  Token never expires"))
            return
        self.stdout.write(self.style.WARNING(f"⏱️  Token expires: {expiry_str}"))
        hours = expires_in / 3600 if expires_in else 0
        message = (
            f"⏱️  Expires in: {hours:.1f} hours"
            if hours < 24
            else f"⏱️  Expires in: {hours / 24:.1f} days"
        )
        self.stdout.write(self.style.WARNING(message))
        if fmt == "json":
            self.stdout.write(
                self.style.NOTICE(
                    f"\nTo use in JS/TS:\nconst tokenData = require('./{out_path}').token;"
                )
            )
        elif fmt == "env":
            self.stdout.write(
                self.style.NOTICE(
                    f"\nTo use: source {out_path} or add to your .env file"
                )
            )
