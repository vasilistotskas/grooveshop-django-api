from product.enum.category import CategoryImageTypeEnum
from product.enum.stock import StockState

# ── Local (single-app) TextChoices/synthetic-status variant maps ──────
# Stock status and reservation status are derived states (not backed
# by a TextChoices field), and category image type only appears in
# this app — kept local rather than promoted to admin.displays.

STOCK_STATUS_VARIANT: dict[str, str] = {
    StockState.OUT_OF_STOCK: "danger",
    StockState.LOW_STOCK: "warning",
    StockState.IN_STOCK: "success",
}

STOCK_RESERVATION_STATUS_VARIANT: dict[str, str] = {
    "consumed": "info",
    "pending": "warning",
}

CATEGORY_IMAGE_TYPE_VARIANT: dict[str, str] = {
    CategoryImageTypeEnum.MAIN: "primary",
    CategoryImageTypeEnum.BANNER: "info",
    CategoryImageTypeEnum.HERO: "info",
    CategoryImageTypeEnum.FEATURE: "warning",
    CategoryImageTypeEnum.PROMOTIONAL: "warning",
    CategoryImageTypeEnum.SEASONAL: "success",
    CategoryImageTypeEnum.ICON: "default",
    CategoryImageTypeEnum.THUMBNAIL: "default",
    CategoryImageTypeEnum.GALLERY: "default",
    CategoryImageTypeEnum.BACKGROUND: "default",
}
