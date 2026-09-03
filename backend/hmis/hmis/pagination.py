from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """
    The default 25 a page, but a screen that means to show a whole history
    can ask for it. Without `page_size_query_param` a client has no way to
    say "all of them" and silently shows the first page as if it were the
    lot — which is how a patient's older vitals went missing from the chart.
    """
    page_size_query_param = "page_size"
    max_page_size = 200
