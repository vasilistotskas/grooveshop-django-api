from core.pagination.count import CountPaginator


class CartPagination(CountPaginator):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 10
    page_query_param = "page"


class CartItemPagination(CountPaginator):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 10
    page_query_param = "page"
