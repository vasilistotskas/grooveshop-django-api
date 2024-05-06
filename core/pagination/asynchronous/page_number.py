import collections
import contextlib

from asgiref.sync import sync_to_async, async_to_sync
from django.template import loader
from django.utils import inspect
from django.utils.encoding import force_str
from rest_framework.exceptions import NotFound
from rest_framework.pagination import BasePagination, _positive_int, \
    _get_displayed_page_numbers, _get_page_links
from rest_framework.response import Response
from django.core.paginator import InvalidPage, PageNotAnInteger, EmptyPage, \
    UnorderedObjectListWarning
from rest_framework.utils.urls import replace_query_param, remove_query_param
import inspect
import warnings
from math import ceil

from django.utils.functional import cached_property
from django.utils.inspect import method_has_no_args
from django.utils.translation import gettext_lazy as _


class AsyncLazyObject:
    def __init__(self, func):
        self._func = func
        self._result = None
        self._called = False

    async def __call__(self):
        if not self._called:
            self._result = await self._func()
            self._called = True
        return self._result


class AsyncPage(collections.abc.Sequence):
    def __init__(self, object_list, number, paginator):
        self.object_list = object_list
        self.number = number
        self.paginator = paginator
        self.total_pages = None

    def __repr__(self):
        total_pages = self.total_pages if self.total_pages is not None else 'Unknown'
        return f"<Page {self.number} of {total_pages}>"

    def __len__(self):
        return len(self.object_list)

    def __getitem__(self, index):
        if not isinstance(index, (int, slice)):
            raise TypeError(
                "Page indices must be integers or slices, not %s."
                % type(index).__name__
            )
        if not isinstance(self.object_list, list):
            self.object_list = list(self.object_list)
        return self.object_list[index]

    async def has_next(self):
        return self.number < await self.paginator.num_pages()

    async def has_previous(self):
        return self.number > 1

    def has_other_pages(self):
        return async_to_sync(self.has_previous)() or async_to_sync(self.has_next)()

    async def next_page_number(self):
        return await self.paginator.validate_number(self.number + 1)

    async def previous_page_number(self):
        return await self.paginator.validate_number(self.number - 1)

    async def start_index(self):
        if await self.paginator.count() == 0:
            return 0
        return (self.paginator.per_page * (self.number - 1)) + 1

    async def end_index(self):
        if self.number == await self.paginator.num_pages():
            return await self.paginator.count()
        return self.number * self.paginator.per_page


class Paginator:
    ELLIPSIS = _("…")
    default_error_messages = {
        "invalid_page": _("That page number is not an integer"),
        "min_page": _("That page number is less than 1"),
        "no_results": _("That page contains no results"),
    }

    def __init__(
        self,
        object_list,
        per_page,
        orphans=0,
        allow_empty_first_page=True,
        error_messages=None,
    ):
        self.object_list = object_list
        self._check_object_list_is_ordered()
        self.per_page = int(per_page)
        self.orphans = int(orphans)
        self.allow_empty_first_page = allow_empty_first_page
        self.error_messages = (
            self.default_error_messages
            if error_messages is None
            else self.default_error_messages | error_messages
        )

    async def __aiter__(self):
        page_range = await self.page_range
        for page_number in page_range:
            yield await self.page(page_number)

    async def validate_number(self, number: int) -> int:
        try:
            if isinstance(number, float) and not number.is_integer():
                raise ValueError
            number = int(number)
        except (TypeError, ValueError):
            raise PageNotAnInteger(self.error_messages["invalid_page"])
        if number < 1:
            raise EmptyPage(self.error_messages["min_page"])
        num_pages = await self.num_pages()
        if number > num_pages:
            raise EmptyPage(self.error_messages["no_results"])
        return number

    async def get_page(self, number: int) -> AsyncPage:
        try:
            number = await self.validate_number(number)
        except PageNotAnInteger:
            number = 1
        except EmptyPage:
            number = await self.num_pages()
        return await self.page(number)

    async def page(self, number: int) -> AsyncPage:
        number = await self.validate_number(number)
        bottom = (number - 1) * self.per_page
        top = bottom + self.per_page
        count = await self.count()
        if top + self.orphans >= count:
            top = count
        object_list_slice = await sync_to_async(self.object_list.__getitem__)(slice(bottom, top))
        return self._get_page(object_list_slice, number, self)

    def _get_page(self, *args, **kwargs):
        return AsyncPage(*args, **kwargs)

    @cached_property
    def count(self):
        async def get_count():
            c = getattr(self.object_list, "acount", None)
            if callable(c) and not inspect.isbuiltin(c) and method_has_no_args(c):
                return await c()
            return await sync_to_async(self.object_list.count)()
        return AsyncLazyObject(get_count)

    @cached_property
    def num_pages(self):
        async def get_num_pages():
            count = await self.count()
            if count == 0 and not self.allow_empty_first_page:
                return 0
            hits = max(1, count - self.orphans)
            return ceil(hits / self.per_page)
        return AsyncLazyObject(get_num_pages)

    @property
    async def page_range(self):
        num_pages = await self.num_pages()
        return range(1, num_pages + 1)

    def _check_object_list_is_ordered(self):
        ordered = getattr(self.object_list, "ordered", None)
        if ordered is not None and not ordered:
            obj_list_repr = (
                "{} {}".format(
                    self.object_list.model, self.object_list.__class__.__name__
                )
                if hasattr(self.object_list, "model")
                else "{!r}".format(self.object_list)
            )
            warnings.warn(
                "Pagination may yield inconsistent results with an unordered "
                "object_list: {}.".format(obj_list_repr),
                UnorderedObjectListWarning,
                stacklevel=3,
            )

    async def get_elided_page_range(self, number=1, *, on_each_side=3, on_ends=2):
        number = await self.validate_number(number)
        num_pages = await self.num_pages()
        page_range = range(1, num_pages + 1)

        if num_pages <= (on_each_side + on_ends) * 2:
            for page in page_range:
                yield page
            return

        if number > (1 + on_each_side + on_ends):
            for page in range(1, on_ends + 1):
                yield page
            yield self.ELLIPSIS

            start_middle = max(on_ends + 1, number - on_each_side)
            end_middle = min(num_pages - on_ends, number + on_each_side) + 1
            for page in range(start_middle, end_middle):
                yield page

        else:
            for page in range(1, number + 1):
                yield page

        if number < (num_pages - on_each_side - on_ends):
            yield self.ELLIPSIS
            for page in range(num_pages - on_ends + 1, num_pages + 1):
                yield page
        else:
            for page in range(number + 1, num_pages + 1):
                yield page


class AsyncPageNumberPaginator(BasePagination):
    page_size_query_param = "page_size"
    max_page_size = 100
    page_size = 2
    django_paginator_class = Paginator
    page_query_param = 'page'
    page_query_description = _('A page number within the paginated result set.')
    page_size_query_description = _('Number of results to return per page.')

    last_page_strings = ('last',)

    template = 'rest_framework/pagination/numbers.html'

    invalid_page_message = _('Invalid page.')

    def __init__(self):
        self.page: AsyncPage | None = None

    async def paginate_queryset(self, queryset, request, view=None):
        self.request = request
        page_size = self.get_page_size(request)
        if not page_size:
            return None

        paginator = self.django_paginator_class(queryset, page_size)
        page_number = await self.get_page_number(request, paginator)

        try:
            self.page = await paginator.page(page_number)
        except InvalidPage as exc:
            msg = self.invalid_page_message.format(
                page_number=page_number, message=str(exc)
            )
            raise NotFound(msg)

        num_pages = await paginator.num_pages()
        self.page.total_pages = num_pages

        if num_pages > 1 and self.template is not None:
            self.display_page_controls = True

        page_list = await sync_to_async(list)(self.page)
        return page_list

    async def get_page_number(self, request, paginator: Paginator):
        page_number = request.query_params.get(self.page_query_param) or 1
        if page_number in self.last_page_strings:
            page_number = await paginator.num_pages()
        return page_number

    async def get_paginated_response(self, data):
        return Response(
            {
                "links": {
                    "next": await self.get_next_link(),
                    "previous": await self.get_previous_link(),
                },
                "count": await self.page.paginator.count(),
                "total_pages": await self.page.paginator.num_pages(),
                "page_size": self.page.paginator.per_page,
                "page_total_results": len(data),
                "page": self.page.number,
                "results": data,
            }
        )

    def get_paginated_response_schema(self, schema):
        return {
            'type': 'object',
            'required': ['count', 'results'],
            'properties': {
                'count': {
                    'type': 'integer',
                    'example': 123,
                },
                'next': {
                    'type': 'string',
                    'nullable': True,
                    'format': 'uri',
                    'example': 'http://api.example.org/accounts/?{page_query_param}=4'.format(
                        page_query_param=self.page_query_param)
                },
                'previous': {
                    'type': 'string',
                    'nullable': True,
                    'format': 'uri',
                    'example': 'http://api.example.org/accounts/?{page_query_param}=2'.format(
                        page_query_param=self.page_query_param)
                },
                'results': schema,
            },
        }

    def get_page_size(self, request):
        if self.page_size_query_param:
            with contextlib.suppress(KeyError, ValueError):
                return _positive_int(
                    request.query_params[self.page_size_query_param],
                    strict=True,
                    cutoff=self.max_page_size
                )
        return self.page_size

    async def get_next_link(self):
        if not await self.page.has_next():
            return None
        url = self.request.build_absolute_uri()
        page_number = await self.page.next_page_number()
        return replace_query_param(url, self.page_query_param, page_number)

    async def get_previous_link(self):
        if not await self.page.has_previous():
            return None
        url = self.request.build_absolute_uri()
        page_number = await self.page.previous_page_number()
        if page_number == 1:
            return remove_query_param(url, self.page_query_param)
        return replace_query_param(url, self.page_query_param, page_number)

    async def get_html_context(self):
        base_url = self.request.build_absolute_uri()

        def page_number_to_url(page_number):
            if page_number == 1:
                return remove_query_param(base_url, self.page_query_param)
            else:
                return replace_query_param(base_url, self.page_query_param, page_number)

        current = self.page.number
        final = await self.page.paginator.num_pages()
        page_numbers = _get_displayed_page_numbers(current, final)
        page_links = _get_page_links(page_numbers, current, page_number_to_url)

        return {
            'previous_url': await self.get_previous_link(),
            'next_url': await self.get_next_link(),
            'page_links': page_links
        }

    def to_html(self):
        template = loader.get_template(self.template)
        context = async_to_sync(self.get_html_context)()
        return template.render(context)

    def get_schema_operation_parameters(self, view):
        parameters = [
            {
                'name': self.page_query_param,
                'required': False,
                'in': 'query',
                'description': force_str(self.page_query_description),
                'schema': {
                    'type': 'integer',
                },
            },
        ]
        if self.page_size_query_param is not None:
            parameters.append(
                {
                    'name': self.page_size_query_param,
                    'required': False,
                    'in': 'query',
                    'description': force_str(self.page_size_query_description),
                    'schema': {
                        'type': 'integer',
                    },
                },
            )
        return parameters
