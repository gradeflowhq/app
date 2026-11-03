from collections.abc import Callable
from functools import wraps

from django.http import HttpResponse
from django.shortcuts import redirect
from django_htmx.http import HttpResponseClientRedirect, retarget

from gradeflow_app.requests import Request, View


def fragment_of(page: str) -> Callable[[View], View]:
    """For a view that renders part of `page`: requests from anywhere but htmx, such as the URL opened in a new tab,
    go to `page` of the same course or assessment, the URL's first argument."""

    def decorate(view: View) -> View:
        @wraps(view)
        def wrapped(request: Request, *args: object, **kwargs: object) -> HttpResponse:
            if request.htmx:
                return view(request, *args, **kwargs)
            return redirect(page, next(iter(request.resolver_match.kwargs.values())))

        return wrapped

    return decorate


def is_fragment(request: Request) -> bool:
    """Whether htmx asks for part of a page, rather than a whole one, as a boosted link does."""
    return request.htmx.request_type == "partial"


def redirect_page(request: Request, url: str) -> HttpResponse:
    """To `url`, as a whole page, from htmx requests too."""
    return HttpResponseClientRedirect(url) if request.htmx else redirect(url)


def morphed_into(response: HttpResponse, target: str) -> HttpResponse:
    """`response` morphed into `target`, in place of what its request aimed at."""
    retarget(response, target)
    response["HX-Reswap"] = "outerMorph"  # django-htmx's reswap() doesn't know htmx 4's morph swaps
    return response
