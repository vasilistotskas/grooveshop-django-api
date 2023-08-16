from core.pagination.limit_offset import LimitOffsetPaginator


class ProductImagePagination(LimitOffsetPaginator):
    default_limit = 20
