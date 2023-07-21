from core.pagination.limit_offset import LimitOffsetPaginator


class ProductImagesPagination(LimitOffsetPaginator):
    default_limit = 20
