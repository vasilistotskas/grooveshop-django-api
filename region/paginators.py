from core.pagination.count import CountPaginator


class RegionPagination(CountPaginator):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 20
    page_query_param = "page"
