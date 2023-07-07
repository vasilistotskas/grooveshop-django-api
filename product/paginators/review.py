from core.pagination.count import CountPaginator


class UserReviewPagination(CountPaginator):
    page_size = 3
    page_size_query_param = "page_size"
    max_page_size = 3
    page_query_param = "page"


class ProductReviewPagination(CountPaginator):
    page_size = 6
    page_size_query_param = "page_size"
    max_page_size = 6
    page_query_param = "page"
