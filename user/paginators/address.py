from core.pagination.count import CountPaginator


class UserAddressPagination(CountPaginator):
    page_size = 3
    page_size_query_param = "page_size"
    max_page_size = 3
    page_query_param = "page"
