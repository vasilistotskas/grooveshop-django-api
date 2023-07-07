from core.pagination.count import CountPaginator


class ProductCategoryPagination(CountPaginator):
    page_size = 36
    page_size_query_param = "page_size"
    max_page_size = 36
    page_query_param = "page"
