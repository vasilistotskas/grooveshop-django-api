from django.apps import apps
from django.db.models import Count, Sum, Avg
from django.db.models.functions import TruncDay
from django.utils import timezone
from django.utils.html import format_html
from django.urls import reverse
from datetime import timedelta


def dashboard_callback(request, context):
    """
    Enhanced dashboard callback for Unfold admin.
    Provides KPIs, chart data, and table data for the admin dashboard.
    """
    User = apps.get_model("user", "UserAccount")
    Product = apps.get_model("product", "Product")
    Order = apps.get_model("order", "Order")
    BlogPost = apps.get_model("blog", "BlogPost")
    BlogComment = apps.get_model("blog", "BlogComment")
    ProductReview = apps.get_model("product", "ProductReview")
    UserSubscription = apps.get_model("user", "UserSubscription")
    Contact = apps.get_model("contact", "Contact")
    Cart = apps.get_model("cart", "Cart")
    ProductCategory = apps.get_model("product", "ProductCategory")

    # Import enums
    from order.enum.status import PaymentStatus, OrderStatus
    from product.enum.review import ReviewStatus

    now = timezone.now()
    today = now.date()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # ========== CORE KPIs ==========
    total_users = User.objects.count()
    total_products = Product.objects.filter(active=True).count()
    total_orders = Order.objects.count()

    # Revenue from completed payments
    revenue_data = Order.objects.filter(
        payment_status=PaymentStatus.COMPLETED
    ).aggregate(total=Sum("paid_amount"))
    total_revenue = revenue_data.get("total") or 0

    # Pending Orders
    pending_orders_count = Order.objects.filter(
        status=OrderStatus.PENDING
    ).count()

    # New users today
    new_users_today = User.objects.filter(created_at__date=today).count()

    # ========== ENGAGEMENT KPIs ==========
    total_blog_views = (
        BlogPost.objects.aggregate(total=Sum("view_count"))["total"] or 0
    )
    pending_reviews_count = ProductReview.objects.filter(
        status=ReviewStatus.NEW
    ).count()
    avg_rating = ProductReview.objects.aggregate(avg=Avg("rate"))["avg"] or 0
    total_subscribers = UserSubscription.objects.filter(status="ACTIVE").count()

    # ========== ADDITIONAL KPIs ==========
    # Low stock products (stock < 10)
    low_stock_count = Product.objects.filter(active=True, stock__lt=10).count()

    # Active carts (updated in last 24 hours with items)
    day_ago = now - timedelta(hours=24)
    active_carts_count = Cart.objects.filter(updated_at__gte=day_ago).count()

    # Total contact messages
    total_messages = Contact.objects.count()

    # Orders this month vs last month (for trend)
    orders_this_month = Order.objects.filter(
        created_at__date__gte=month_ago
    ).count()

    # ========== BLOG KPIs ==========
    total_blog_posts = BlogPost.objects.count()
    published_posts = BlogPost.objects.filter(is_published=True).count()
    featured_posts = BlogPost.objects.filter(featured=True).count()
    pending_blog_comments = BlogComment.objects.filter(approved=False).count()
    total_blog_comments = BlogComment.objects.count()

    # Product categories
    total_categories = ProductCategory.objects.filter(active=True).count()

    # ========== CHART DATA: Last 7 Days ==========

    # Generate labels for last 7 days
    labels_7d = []
    for i in range(7):
        day = (now - timedelta(days=6 - i)).strftime("%a")
        labels_7d.append(day)

    # Users chart
    users_by_day = (
        User.objects.filter(created_at__gte=week_ago)
        .annotate(day=TruncDay("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    users_dict = {
        item["day"].strftime("%Y-%m-%d"): item["count"]
        for item in users_by_day
        if item["day"]
    }

    users_data = []
    for i in range(7):
        day_str = (now - timedelta(days=6 - i)).strftime("%Y-%m-%d")
        users_data.append(users_dict.get(day_str, 0))

    # Orders chart
    orders_by_day = (
        Order.objects.filter(created_at__gte=week_ago)
        .annotate(day=TruncDay("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    orders_dict = {
        item["day"].strftime("%Y-%m-%d"): item["count"]
        for item in orders_by_day
        if item["day"]
    }

    orders_data = []
    for i in range(7):
        day_str = (now - timedelta(days=6 - i)).strftime("%Y-%m-%d")
        orders_data.append(orders_dict.get(day_str, 0))

    # Revenue chart (daily revenue from completed orders)
    revenue_by_day = (
        Order.objects.filter(
            created_at__gte=week_ago, payment_status=PaymentStatus.COMPLETED
        )
        .annotate(day=TruncDay("created_at"))
        .values("day")
        .annotate(total=Sum("paid_amount"))
        .order_by("day")
    )
    revenue_dict = {
        item["day"].strftime("%Y-%m-%d"): float(item["total"] or 0)
        for item in revenue_by_day
        if item["day"]
    }

    revenue_data_chart = []
    for i in range(7):
        day_str = (now - timedelta(days=6 - i)).strftime("%Y-%m-%d")
        revenue_data_chart.append(revenue_dict.get(day_str, 0))

    # Combined chart for orders and revenue
    performance_chart = {
        "labels": labels_7d,
        "datasets": [
            {
                "label": "Orders",
                "type": "bar",
                "data": orders_data,
                "backgroundColor": "oklch(70% 0.15 270)",  # Violet
                "borderRadius": 4,
                "yAxisID": "y",
            },
            {
                "label": "Revenue (€)",
                "type": "line",
                "data": revenue_data_chart,
                "borderColor": "oklch(65% 0.2 145)",  # Green
                "backgroundColor": "oklch(65% 0.2 145 / 0.1)",
                "fill": True,
                "tension": 0.4,
                "yAxisID": "y1",
            },
        ],
    }

    # Users growth chart
    users_chart = {
        "labels": labels_7d,
        "datasets": [
            {
                "label": "New Users",
                "data": users_data,
                "backgroundColor": "oklch(65% 0.15 280)",
                "borderColor": "oklch(55% 0.2 280)",
                "borderWidth": 2,
                "borderRadius": 6,
            }
        ],
    }

    # Order Status Distribution (Doughnut)
    status_counts = Order.objects.values("status").annotate(count=Count("id"))
    status_labels = []
    status_data = []
    status_colors = {
        "PENDING": "oklch(75% 0.15 85)",  # Yellow
        "COMPLETED": "oklch(70% 0.15 145)",  # Green
        "PROCESSING": "oklch(65% 0.15 250)",  # Blue
        "SHIPPED": "oklch(70% 0.12 200)",  # Cyan
        "CANCELLED": "oklch(65% 0.2 25)",  # Red
        "CANCELED": "oklch(65% 0.2 25)",  # Red (alt spelling)
    }
    colors = []

    for item in status_counts:
        status_labels.append(item["status"].replace("_", " ").title())
        status_data.append(item["count"])
        colors.append(status_colors.get(item["status"], "oklch(60% 0.05 250)"))

    status_chart = {
        "labels": status_labels,
        "datasets": [
            {
                "data": status_data,
                "backgroundColor": colors,
                "borderWidth": 0,
                "spacing": 2,
            }
        ],
    }

    # 1. Payment Method Distribution (Pie Chart)
    # Group by the 'pay_way' relationship to get the nice name, fallback to payment_method field
    payment_counts = (
        Order.objects.values("pay_way__translations__name")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    pay_labels = []
    pay_data = []
    # Vibrant palette for payment methods
    pay_colors = [
        "oklch(65% 0.18 200)",  # Cyan
        "oklch(70% 0.15 300)",  # Magenta
        "oklch(75% 0.15 60)",  # Orange
        "oklch(60% 0.12 270)",  # Purple
        "oklch(80% 0.12 100)",  # Yellow-Green
    ]

    for item in payment_counts:
        name = item.get("pay_way__translations__name") or "Unknown"
        pay_labels.append(name)
        pay_data.append(item["count"])

    payment_chart = {
        "labels": pay_labels,
        "datasets": [
            {
                "data": pay_data,
                "backgroundColor": pay_colors[: len(pay_data)]
                if len(pay_data) <= len(pay_colors)
                else pay_colors * (len(pay_data) // len(pay_colors) + 1),
                "borderWidth": 0,
            }
        ],
    }

    # 2. Top Countries by Order Volume (Bar Chart)
    country_counts = (
        Order.objects.values("country__alpha_2")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    country_labels = []
    country_data = []

    for item in country_counts:
        code = item.get("country__alpha_2") or "Other"
        country_labels.append(code.upper())
        country_data.append(item["count"])

    country_chart = {
        "labels": country_labels,
        "datasets": [
            {
                "label": "Orders",
                "data": country_data,
                "backgroundColor": "oklch(65% 0.15 260)",  # Blue-ish
                "borderRadius": 4,
                "barThickness": 20,
            }
        ],
    }

    # 3. Average Cart Value (KPI)
    # Calculate average of total_price for active carts.
    # Since total_price is a property, we can't aggregate it directly in DB easily without complex queries.
    # We will approximate this by aggregating the items' prices if possible, or iterate a small sample.
    # BETTER APPROACH: For a dashboard, let's use the 'Order' average value as a proxy for "Cart Value" potential,
    # OR if we strictly want Active Carts, we check the CartItem model.
    # Let's stick to Average Order Value (AOV) as it's a solid business metric.
    avg_order_value_data = Order.objects.aggregate(avg=Avg("paid_amount"))
    avg_order_value = avg_order_value_data.get("avg") or 0

    # 4. Total Blog Likes (KPI)
    # BlogPost has many-to-many to User via 'likes'
    # Aggregating M2M count efficiently
    total_blog_likes = (
        BlogPost.objects.aggregate(total_likes=Count("likes"))["total_likes"]
        or 0
    )

    # ========== TABLE DATA (Unfold format) ==========

    # Recent Orders Table
    recent_orders = Order.objects.select_related("user").order_by(
        "-created_at"
    )[:5]
    orders_table_rows = []
    for order in recent_orders:
        status_badge = _get_status_badge(order.status)
        orders_table_rows.append(
            [
                format_html(
                    '<a href="{}" class="text-primary-600 dark:text-primary-400 font-medium hover:underline">#{}</a>',
                    reverse("admin:order_order_change", args=[order.id]),
                    order.id,
                ),
                order.email or "N/A",
                order.created_at.strftime("%b %d, %Y"),
                status_badge,
            ]
        )

    orders_table = {
        "headers": ["Order #", "Customer", "Date", "Status"],
        "rows": orders_table_rows,
    }

    # Top Products Table
    top_products = Product.objects.order_by("-view_count")[:5]
    products_table_rows = []
    for product in top_products:
        name = (
            product.safe_translation_getter("name", any_language=True)
            or "Unnamed"
        )
        stock_badge = _get_stock_badge(product.stock)
        products_table_rows.append(
            [
                format_html(
                    '<a href="{}" class="text-primary-600 dark:text-primary-400 font-medium hover:underline">{}</a>',
                    reverse("admin:product_product_change", args=[product.id]),
                    name[:30],
                ),
                format_html(
                    '<span class="font-semibold">{}</span>', product.view_count
                ),
                stock_badge,
            ]
        )

    products_table = {
        "headers": ["Product", "Views", "Stock"],
        "rows": products_table_rows,
    }

    # Recent Reviews Table
    recent_reviews = ProductReview.objects.select_related(
        "product", "user"
    ).order_by("-created_at")[:5]
    reviews_table_rows = []
    for review in recent_reviews:
        product_name = (
            review.product.safe_translation_getter("name", any_language=True)
            or "Unknown"
        )
        rating_stars = _get_rating_stars(review.rate)
        status_badge = _get_review_status_badge(review.status)
        reviews_table_rows.append(
            [
                product_name[:25],
                review.user.email[:25] if review.user else "Anonymous",
                rating_stars,
                status_badge,
            ]
        )

    reviews_table = {
        "headers": ["Product", "User", "Rating", "Status"],
        "rows": reviews_table_rows,
    }

    # Recent Messages Table
    recent_messages = Contact.objects.order_by("-created_at")[:5]
    messages_table_rows = []
    for msg in recent_messages:
        messages_table_rows.append(
            [
                format_html(
                    '<div class="font-medium">{}</div><div class="text-xs text-base-600 dark:text-base-300">{}</div>',
                    msg.name,
                    msg.email,
                ),
                msg.created_at.strftime("%b %d"),
                msg.message[:40] + "..."
                if len(msg.message) > 40
                else msg.message,
            ]
        )

    messages_table = {
        "headers": ["From", "Date", "Message"],
        "rows": messages_table_rows,
    }

    # Low Stock Products Table
    low_stock_products = Product.objects.filter(
        active=True, stock__lt=10
    ).order_by("stock")[:5]
    low_stock_rows = []
    for product in low_stock_products:
        name = (
            product.safe_translation_getter("name", any_language=True)
            or "Unnamed"
        )
        stock_badge = _get_stock_badge(product.stock)
        low_stock_rows.append(
            [
                format_html(
                    '<a href="{}" class="text-primary-600 dark:text-primary-400 hover:underline">{}</a>',
                    reverse("admin:product_product_change", args=[product.id]),
                    name[:30],
                ),
                stock_badge,
            ]
        )

    low_stock_table = {
        "headers": ["Product", "Stock"],
        "rows": low_stock_rows,
    }

    # ========== PROGRESS BARS ==========

    # Inventory health (percentage of products in stock)
    total_active_products = Product.objects.filter(active=True).count()
    in_stock_products = Product.objects.filter(active=True, stock__gt=0).count()
    inventory_health = (
        (in_stock_products / total_active_products * 100)
        if total_active_products > 0
        else 0
    )

    inventory_progress = {
        "title": "Inventory Health",
        "description": f"{in_stock_products}/{total_active_products} products in stock",
        "value": round(inventory_health, 1),
    }

    # ========== QUICK LINKS ==========
    quick_links = [
        {
            "title": "Add Product",
            "url": reverse("admin:product_product_add"),
            "icon": "add_circle",
        },
        {
            "title": "View Orders",
            "url": reverse("admin:order_order_changelist"),
            "icon": "shopping_cart",
        },
        {
            "title": "Manage Users",
            "url": reverse("admin:user_useraccount_changelist"),
            "icon": "group",
        },
        {
            "title": "Blog Posts",
            "url": reverse("admin:blog_blogpost_changelist"),
            "icon": "article",
        },
    ]

    # ========== UPDATE CONTEXT ==========
    context.update(
        {
            # Core KPIs
            "kpi": {
                "users": total_users,
                "new_users_today": new_users_today,
                "products": total_products,
                "orders": total_orders,
                "revenue": total_revenue,
                "pending_orders": pending_orders_count,
                "blog_views": total_blog_views,
                "pending_reviews": pending_reviews_count,
                "avg_rating": round(avg_rating, 1) if avg_rating else 0,
                "subscribers": total_subscribers,
                "low_stock": low_stock_count,
                "active_carts": active_carts_count,
                "messages": total_messages,
                "orders_this_month": orders_this_month,
                "avg_order_value": round(avg_order_value, 2),
                "total_blog_likes": total_blog_likes,
                # Blog KPIs
                "blog_posts": total_blog_posts,
                "published_posts": published_posts,
                "featured_posts": featured_posts,
                "pending_comments": pending_blog_comments,
                "total_comments": total_blog_comments,
                # Categories
                "categories": total_categories,
            },
            # Charts (JSON strings for Chart.js)
            "performance_chart": performance_chart,
            "users_chart": users_chart,
            "status_chart": status_chart,
            "payment_chart": payment_chart,
            "country_chart": country_chart,
            # Tables (Unfold format)
            "orders_table": orders_table,
            "products_table": products_table,
            "reviews_table": reviews_table,
            "messages_table": messages_table,
            "low_stock_table": low_stock_table,
            # Progress bars
            "inventory_progress": inventory_progress,
            # Quick links
            "quick_links": quick_links,
            # Legacy support for existing template parts
            "recent_orders": recent_orders,
            "top_products": top_products,
            "recent_reviews": recent_reviews,
            "recent_messages": recent_messages,
        }
    )

    return context


def _get_status_badge(status):
    """Generate HTML badge for order status."""
    colors = {
        "PENDING": "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300",
        "PROCESSING": "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300",
        "SHIPPED": "bg-cyan-100 text-cyan-800 dark:bg-cyan-900 dark:text-cyan-300",
        "COMPLETED": "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
        "CANCELLED": "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300",
        "CANCELED": "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300",
    }
    color_class = colors.get(
        status, "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300"
    )
    label = status.replace("_", " ").title()
    return format_html(
        '<span class="px-2 py-1 text-xs font-semibold rounded-full {}">{}</span>',
        color_class,
        label,
    )


def _get_stock_badge(stock):
    """Generate HTML badge for stock level."""
    if stock == 0:
        return format_html(
            '<span class="px-2 py-1 text-xs font-bold rounded-full bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300">Out of Stock</span>'
        )
    elif stock < 10:
        return format_html(
            '<span class="px-2 py-1 text-xs font-bold rounded-full bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300">{}</span>',
            stock,
        )
    else:
        return format_html(
            '<span class="px-2 py-1 text-xs font-bold rounded-full bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300">{}</span>',
            stock,
        )


def _get_rating_stars(rate):
    """Generate star rating display."""
    filled = "★" * rate
    empty = "☆" * (5 - rate)
    return format_html(
        '<span class="text-yellow-500 font-mono">{}{}</span>', filled, empty
    )


def _get_review_status_badge(status):
    """Generate HTML badge for review status."""
    colors = {
        "NEW": "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300",
        "APPROVED": "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
        "REJECTED": "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300",
    }
    color_class = colors.get(
        status, "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300"
    )
    label = status.replace("_", " ").title()
    return format_html(
        '<span class="px-2 py-1 text-xs font-semibold rounded-full {}">{}</span>',
        color_class,
        label,
    )
