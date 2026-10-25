from typing import Annotated

from fastapi import APIRouter, Query

from code_mortis.api.dependencies import DB, USER
from code_mortis.reviews import analytics
from code_mortis.schemas.analytics import (
    AnalyticsFilters,
    AnalyticsFindingFilters,
    AnalyticsFindings,
    AnalyticsOverview,
    AnalyticsReviews,
    AnalyticsSecurity,
    AnalyticsUsage,
)
from code_mortis.security_scans.analytics import security

router = APIRouter()


@router.get("/analytics/overview", response_model=AnalyticsOverview)
def analytics_overview(db: DB, user: USER, filters: Annotated[AnalyticsFilters, Query()]):
    return analytics.overview(analytics.Scope(db, user, filters))


@router.get("/analytics/findings", response_model=AnalyticsFindings)
def analytics_findings(db: DB, user: USER, filters: Annotated[AnalyticsFindingFilters, Query()]):
    return analytics.findings(analytics.Scope(db, user, filters))


@router.get("/analytics/reviews", response_model=AnalyticsReviews)
def analytics_reviews(db: DB, user: USER, filters: Annotated[AnalyticsFilters, Query()]):
    return analytics.reviews(analytics.Scope(db, user, filters))


@router.get("/analytics/usage", response_model=AnalyticsUsage)
def analytics_usage(db: DB, user: USER, filters: Annotated[AnalyticsFilters, Query()]):
    return analytics.usage(analytics.Scope(db, user, filters))


@router.get("/analytics/security", response_model=AnalyticsSecurity)
def analytics_security(db: DB, user: USER, filters: Annotated[AnalyticsFilters, Query()]):
    return security(db, user, filters)
