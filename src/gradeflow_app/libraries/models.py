import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.db.models import Q
from gradeflow_core import GradeFlowError
from gradeflow_core.editing import method_name, method_ref, split_ref

from gradeflow_app.courses.models import Course

if TYPE_CHECKING:
    from gradeflow_app.libraries.method_tests import MethodTests

NAMESPACE_LENGTH = 60
NAME_LENGTH = 100


class Library(models.Model):
    """A person's or a course's saved methods, named by its namespace, which is made with its owner. It outlives
    its owner, whose methods in use stay there, and the namespace stays taken."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    namespace = models.SlugField(max_length=NAMESPACE_LENGTH, unique=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="library"
    )
    course = models.OneToOneField(Course, null=True, blank=True, on_delete=models.SET_NULL, related_name="library")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = (
            models.CheckConstraint(condition=Q(user__isnull=True) | Q(course__isnull=True), name="one_library_owner"),
        )

    def __str__(self) -> str:
        return self.namespace

    @property
    def owner_name(self) -> str:
        """Who made its methods, as the picker shows them: "by Introductory Biology"."""
        return str(self.course or self.user or "a former member")


class Method(models.Model):
    """A method people made, in a library, published in numbered versions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    library = models.ForeignKey(Library, on_delete=models.PROTECT, related_name="methods")
    name = models.SlugField(max_length=NAME_LENGTH)
    public = models.BooleanField(default=False)
    archived = models.BooleanField(default=False)
    """Offered nowhere; what uses it keeps working."""
    next_version_number = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (models.UniqueConstraint(fields=("library", "name"), name="one_name_per_library"),)
        ordering = ("name",)

    def __str__(self) -> str:
        return self.full_name

    @property
    def full_name(self) -> str:
        """How checks name it, with a version: ``intro-biology/term-not-look-alike@3``."""
        return method_name(self.library.namespace, self.name)

    @property
    def latest_version(self) -> MethodVersion | None:
        return self.versions.order_by("-number").first()


class VersionQuerySet(models.QuerySet["MethodVersion"]):
    def named(self, refs: Iterable[str]) -> VersionQuerySet:
        """The saved versions `refs` name, skipping any that isn't a ref. The core's own methods match none: no
        library has their namespace."""
        query = Q(pk__in=[])
        for ref in refs:
            try:
                namespace, name, number = split_ref(ref)
            except GradeFlowError:
                continue
            query |= Q(method__library__namespace=namespace, method__name=name, number=number)
        return self.filter(query)

    def with_used_versions(self) -> VersionQuerySet:
        """These versions and every version they use."""
        chosen = self.values("pk")
        return self.model.objects.filter(
            Q(pk__in=chosen) | Q(pk__in=VersionUse.objects.filter(version__in=chosen).values("used_version"))
        )

    def linked(self, refs: Iterable[str]) -> list[MethodVersion]:
        """The versions `refs` name and those they use, locked against deletion and renaming until what links them
        commits: the database checks the links only then. They're read again once locked, so a rename that held
        the lock leaves out what it renamed."""
        versions = self.named(refs).with_used_versions()
        list(versions.select_for_update(no_key=True, of=("self",)))
        return list(versions.select_related("method__library"))


class MethodVersion(models.Model):
    """A published version of a method, which never changes, with the tests it was published with."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    method = models.ForeignKey(Method, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    definition = models.TextField()
    used_versions = models.ManyToManyField("self", symmetrical=False, through="VersionUse", related_name="used_by")
    """Every saved version it needs, directly or through others."""
    tests = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = VersionQuerySet.as_manager()

    class Meta:
        constraints = (models.UniqueConstraint(fields=("method", "number"), name="one_number_per_method"),)
        ordering = ("method__name", "-number")

    def __str__(self) -> str:
        return self.ref

    @property
    def ref(self) -> str:
        return method_ref(self.method.full_name, self.number)


class VersionUse(models.Model):
    """A version a version needs, which can't be deleted while it does."""

    version = models.ForeignKey(MethodVersion, on_delete=models.CASCADE, related_name="+")
    used_version = models.ForeignKey(MethodVersion, on_delete=models.RESTRICT, related_name="+")

    class Meta:
        constraints = (models.UniqueConstraint(fields=("version", "used_version"), name="one_version_use"),)

    def __str__(self) -> str:
        return f"{self.version_id} uses {self.used_version_id}"


class TestsRecord(models.Model):
    """A person's test answers for a method, and how the last run that ended marked them. ``revision`` changes with
    every edit, so a run of an earlier revision is out of date."""

    tests = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=0)
    run_revision = models.PositiveIntegerField(null=True, blank=True)
    """The revision the last run that ended marked."""
    results = models.JSONField(null=True, blank=True)
    """What that run gave each test answer, unless it failed."""
    failure = models.TextField(blank=True)
    """Why that run failed."""
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def save_revision(self, tests: MethodTests) -> None:
        """Save `tests`, with any other change made to it, as the next revision."""
        self.tests = dict(tests)
        self.revision += 1
        self.save()


class MethodDraftRecord(TestsRecord):
    """A person's unpublished changes to a method, or a new method before its first version."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    method = models.ForeignKey(Method, on_delete=models.CASCADE, related_name="drafts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="method_drafts")
    based_on = models.ForeignKey(MethodVersion, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    definition = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = (models.UniqueConstraint(fields=("user", "method"), name="one_method_draft_per_user"),)

    def __str__(self) -> str:
        return f"{self.method} draft by {self.user}"


class VersionTests(TestsRecord):
    """A person's tests of a published version, which leave the version as it is."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    version = models.ForeignKey(MethodVersion, on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="version_tests")

    class Meta:
        constraints = (models.UniqueConstraint(fields=("user", "version"), name="one_version_tests_per_user"),)

    def __str__(self) -> str:
        return f"{self.version} tests by {self.user}"
