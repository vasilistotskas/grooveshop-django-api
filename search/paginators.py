from core.pagination.count import CountPaginator


class SearchPagination(CountPaginator):
    page_size = 16
    page_size_query_param = "page_size"
    max_page_size = 50
    page_query_param = "page"
