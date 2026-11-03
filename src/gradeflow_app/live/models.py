from django.db import models


class Topic(models.Model):
    """When the data behind topic `name` last changed, so pages listening to it know to refresh."""

    name = models.CharField(primary_key=True, max_length=21)
    changed_at = models.DateTimeField()

    def __str__(self) -> str:
        return self.name
