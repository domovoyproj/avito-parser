"""One parameterized catalog predicate for pages and both export paths."""

import re


def search_mode(query: str | None) -> tuple[str, str | None]:
    text = (query or "").strip()
    if not text:
        return "none", None
    if len(text) > 200:
        raise ValueError("Поисковый запрос слишком длинный")
    tokens = re.findall(r"\w+", text, flags=re.UNICODE)
    # FTS prefix search also handles one-character Cyrillic queries case-insensitively.
    # Punctuation keeps literal substring semantics.
    if not tokens or len(tokens) > 8 or "_" in text or re.search(r"[^\w\s]", text, flags=re.UNICODE):
        return "literal", text
    return "fts", " ".join('"' + token.replace('"', '""') + '"*' for token in tokens)


def catalog_where(*, search_query_id=None, query=None, min_price=None, max_price=None,
                  with_discount_only=False, with_delivery_only=False, favorites_only=False,
                  hot_deals_only=False, gems_only=False, hide_reserved=False, deal_grade=None,
                  min_deal_score=None, include_hidden=False, user_id=None):
    clauses = ["(is_closed=0 OR is_closed IS NULL)"]
    params = []
    if not include_hidden:
        clauses.append("(is_hidden=0 OR is_hidden IS NULL)")
    if hide_reserved:
        clauses.append("(is_reserved=0 OR is_reserved IS NULL)")
    if favorites_only:
        if user_id is None:
            clauses.append("is_favorite=1")
        else:
            clauses.append("EXISTS (SELECT 1 FROM watchlist_items wi JOIN watchlists w ON w.id=wi.watchlist_id WHERE wi.item_id=items.id AND w.user_id=? AND w.is_default=1)")
            params.append(user_id)
    if gems_only:
        clauses.append("deal_grade='GEM'")
    elif deal_grade:
        grade = deal_grade.upper()
        if grade == "HOT":
            clauses.append("deal_grade IN ('GEM','HOT')")
        elif grade == "FAIR":
            clauses.append("deal_grade IN ('GEM','HOT','FAIR')")
        else:
            clauses.append("deal_grade=?")
            params.append(grade)
    elif hot_deals_only:
        clauses.append("(is_hot_deal=1 OR deal_grade IN ('GEM','HOT'))")
    if min_deal_score is not None and int(min_deal_score) > 0:
        clauses.append("deal_score>=?")
        params.append(int(min_deal_score))
    if search_query_id is not None and str(search_query_id) != "":
        if str(search_query_id) in ("-1", "unassigned", "null"):
            clauses.append("search_query_id IS NULL")
        else:
            try:
                search_id = int(search_query_id)
            except (TypeError, ValueError):
                pass
            else:
                clauses.append("EXISTS (SELECT 1 FROM item_searches m WHERE m.item_id=items.id AND m.search_id=?)")
                params.append(search_id)
    mode, value = search_mode(query)
    if mode == "fts":
        clauses.append("items.rowid IN (SELECT rowid FROM item_fts WHERE item_fts MATCH ?)")
        params.append(value)
    elif mode == "literal":
        clauses.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR address LIKE ? ESCAPE '\\' OR params_json LIKE ? ESCAPE '\\')")
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.extend([f"%{escaped}%"] * 4)
    if min_price is not None:
        clauses.append("price>=?")
        params.append(min_price)
    if max_price is not None:
        clauses.append("price<=?")
        params.append(max_price)
    if with_discount_only:
        clauses.append("(old_price IS NOT NULL AND price<old_price)")
    if with_delivery_only:
        clauses.append("delivery_available=1")
    return " AND ".join(clauses), params, mode, value
