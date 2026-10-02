"""Credentials for external clients that access this workspace."""

from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils.timezone import now

from zerver.models.realms import Realm


class MCPClient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    redirect_uris = models.JSONField(default=list)
    created_at = models.DateTimeField(default=now)


class MCPAccessGrant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    client = models.ForeignKey(MCPClient, null=True, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    scopes = models.JSONField(default=list)
    resource = models.URLField(max_length=2048)
    created_at = models.DateTimeField(default=now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, default=None)
    last_used_at = models.DateTimeField(null=True, default=None)


class MCPAuthorizationCode(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    grant = models.ForeignKey(MCPAccessGrant, on_delete=models.CASCADE)
    redirect_uri = models.URLField(max_length=2048)
    challenge = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, default=None)


class MCPAccessToken(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    grant = models.ForeignKey(MCPAccessGrant, on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, choices=[("access", "access"), ("refresh", "refresh")])
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, default=None)


class MCPAccessAudit(models.Model):
    grant = models.ForeignKey(MCPAccessGrant, on_delete=models.CASCADE)
    tool_name = models.CharField(max_length=100)
    outcome = models.CharField(max_length=20)
    created_at = models.DateTimeField(default=now)
