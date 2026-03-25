from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from .forms import COUNTRY_CHOICES  # module-level — required for test patching

mcp = FastMCP("RespectASO")


# ---------------------------------------------------------------------------
# Read Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_apps() -> list[dict]:
    """List all tracked iOS apps."""
    from .models import App
    return [
        {
            "id": a.id,
            "name": a.name,
            "bundle_id": a.bundle_id,
            "track_id": a.track_id,
            "store_url": a.store_url,
            "icon_url": a.icon_url,
            "seller_name": a.seller_name,
        }
        for a in App.objects.all()
    ]


@mcp.tool()
def list_keywords(app_id: int | None = None) -> list[dict]:
    """
    List tracked keywords with their latest score per country.

    If app_id is provided, returns only keywords linked to that app.
    """
    from django.db.models import Max
    from .models import Keyword, SearchResult

    qs = Keyword.objects.select_related("app").all()
    if app_id is not None:
        qs = qs.filter(app_id=app_id)

    results = []
    for kw in qs:
        # Latest result ID per country for this keyword
        latest_ids = (
            SearchResult.objects.filter(keyword=kw)
            .values("country")
            .annotate(latest_id=Max("id"))
            .values_list("latest_id", flat=True)
        )
        latest_results = SearchResult.objects.filter(id__in=latest_ids).order_by("country")
        results.append({
            "id": kw.id,
            "keyword": kw.keyword,
            "app_id": kw.app_id,
            "app_name": kw.app.name if kw.app else None,
            "results": [
                {
                    "country": r.country,
                    "popularity_score": r.popularity_score,
                    "difficulty_score": r.difficulty_score,
                    "difficulty_label": r.difficulty_label,
                    "searched_at": r.searched_at.isoformat(),
                }
                for r in latest_results
            ],
        })
    return results


@mcp.tool()
def get_keyword_scores(keyword_id: int, country: str | None = None) -> dict:
    """
    Get the latest scores for a keyword.

    Returns popularity, difficulty, targeting advice, competitor data, and app rank.
    If country is omitted, returns the most recently searched country's result.
    """
    from .models import Keyword, SearchResult

    try:
        kw = Keyword.objects.get(id=keyword_id)
    except Keyword.DoesNotExist:
        return {"error": f"Keyword {keyword_id} not found"}

    qs = SearchResult.objects.filter(keyword=kw)
    if country:
        qs = qs.filter(country=country)

    result = qs.order_by("-searched_at").first()
    if not result:
        return {"error": f"No results found for keyword {keyword_id}" + (f" in {country}" if country else "")}

    _, advice_label, _, advice_desc = result.targeting_advice
    return {
        "keyword": kw.keyword,
        "country": result.country,
        "popularity_score": result.popularity_score,
        "difficulty_score": result.difficulty_score,
        "difficulty_label": result.difficulty_label,
        "targeting_advice": {"label": advice_label, "description": advice_desc},
        "competitors_data": result.competitors_data,
        "app_rank": result.app_rank,
        "searched_at": result.searched_at.isoformat(),
    }


@mcp.tool()
def get_keyword_trend(keyword_id: int, country: str | None = None) -> list[dict]:
    """
    Get historical trend data for a keyword.

    Returns a list of data points ordered by date. Optionally filter by country.
    """
    from .models import Keyword, SearchResult

    try:
        kw = Keyword.objects.get(id=keyword_id)
    except Keyword.DoesNotExist:
        return [{"error": f"Keyword {keyword_id} not found"}]

    qs = SearchResult.objects.filter(keyword=kw).order_by("searched_at")
    if country:
        qs = qs.filter(country=country)

    return [
        {
            "date": r.searched_at.strftime("%Y-%m-%d"),
            "popularity": r.popularity_score,
            "difficulty": r.difficulty_score,
            "rank": r.app_rank,
            "country": r.country,
        }
        for r in qs
    ]


@mcp.tool()
def get_search_history(
    app_id: int | None = None,
    country: str | None = None,
    page: int = 1,
) -> dict:
    """
    Get paginated search history (latest result per keyword+country).

    Returns 25 results per page.
    """
    from django.db.models import Max
    from .models import SearchResult

    filter_kwargs: dict = {}
    if app_id is not None:
        filter_kwargs["keyword__app_id"] = app_id
    if country:
        filter_kwargs["country"] = country.lower()

    latest_ids = (
        SearchResult.objects.filter(**filter_kwargs)
        .values("keyword_id", "country")
        .annotate(latest_id=Max("id"))
        .values_list("latest_id", flat=True)
    )

    qs = (
        SearchResult.objects.filter(id__in=list(latest_ids))
        .select_related("keyword", "keyword__app")
        .order_by("-searched_at")
    )

    per_page = 25
    total_count = qs.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page

    results = [
        {
            "keyword": r.keyword.keyword,
            "app_name": r.keyword.app.name if r.keyword.app else None,
            "country": r.country,
            "popularity_score": r.popularity_score,
            "difficulty_score": r.difficulty_score,
            "difficulty_label": r.difficulty_label,
            "app_rank": r.app_rank,
            "searched_at": r.searched_at.isoformat(),
        }
        for r in qs[offset : offset + per_page]
    ]

    return {
        "results": results,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
