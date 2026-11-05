from django import template
from django.utils.html import json_script
from django.utils.safestring import SafeString

register = template.Library()


@register.simple_tag(takes_context=True)
def account_keys(context: template.Context) -> SafeString | str:
    user = context["request"].user
    if not user.is_authenticated:
        return ""
    account = {
        "userId": str(user.pk),
        "kdfSalt": user.kdf_salt,
        "publicKey": user.public_key,
        "wrappedPrivateKey": user.wrapped_private_key,
        "encryptedSecrets": dict(user.secrets.values_list("name", "encrypted_value")),
    }
    return json_script(account, "account-keys")
