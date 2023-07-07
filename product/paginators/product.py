from core.pagination.limit_offset import LimitOffsetPaginator


class ProductPagination(LimitOffsetPaginator):
    default_limit = 54


class ProductImagesPagination(LimitOffsetPaginator):
    default_limit = 20
